use super::*;

impl<'a> Shape<'a> {
    pub fn copy(&self) -> Result<Self> {
        self.engine
            .own(call!(self.engine,"GEOSGeom_clone_r",(Ptr,Ptr)->Ptr,self.engine.context,self.raw))
    }
    fn binary(&self, other: &Self, name: &'static [u8]) -> Result<Self> {
        let f = unsafe {
            self.engine
                .library
                .get::<unsafe extern "C" fn(Ptr, Ptr, Ptr) -> Ptr>(name)
        }
        .map_err(|e| e.to_string())?;
        let started = Instant::now();
        let raw = unsafe { f(self.engine.context, self.raw, other.raw) };
        self.engine.record(
            std::str::from_utf8(&name[..name.len() - 1]).unwrap(),
            started.elapsed(),
        );
        self.engine.own(raw)
    }
    pub fn intersection(&self, other: &Self) -> Result<Self> {
        self.binary(other, b"GEOSIntersection_r\0")
    }
    pub fn difference(&self, other: &Self) -> Result<Self> {
        self.binary(other, b"GEOSDifference_r\0")
    }
    pub fn buffer(&self, distance: f64) -> Result<Self> {
        self.engine
            .own(call!(self.engine,"GEOSBuffer_r",(Ptr,Ptr,f64,c_int)->Ptr,
            self.engine.context,self.raw,distance,32))
    }
    fn predicate(&self, other: &Self, name: &'static [u8]) -> Result<bool> {
        let f = unsafe {
            self.engine
                .library
                .get::<unsafe extern "C" fn(Ptr, Ptr, Ptr) -> c_char>(name)
        }
        .map_err(|e| e.to_string())?;
        let started = Instant::now();
        let value = unsafe { f(self.engine.context, self.raw, other.raw) };
        self.engine.record(
            std::str::from_utf8(&name[..name.len() - 1]).unwrap(),
            started.elapsed(),
        );
        match value {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err("native GEOS predicate failed".into()),
        }
    }
    pub fn covers(&self, other: &Self) -> Result<bool> {
        self.predicate(other, b"GEOSCovers_r\0")
    }
    pub fn equals(&self, other: &Self) -> Result<bool> {
        self.predicate(other, b"GEOSEquals_r\0")
    }
    pub fn empty(&self) -> Result<bool> {
        match call!(self.engine,"GEOSisEmpty_r",(Ptr,Ptr)->c_char,self.engine.context,self.raw) {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err("native GEOS empty check failed".into()),
        }
    }
    pub fn valid(&self) -> Result<bool> {
        Ok(call!(self.engine,"GEOSisValid_r",(Ptr,Ptr)->c_char,self.engine.context,self.raw) == 1)
    }
    fn scalar(&self, name: &[u8]) -> Result<f64> {
        let f = unsafe {
            self.engine
                .library
                .get::<unsafe extern "C" fn(Ptr, Ptr, *mut f64) -> c_int>(name)
        }
        .map_err(|e| e.to_string())?;
        let mut value = 0.;
        if unsafe { f(self.engine.context, self.raw, &mut value) } == 0 {
            return Err("GEOS scalar failed".into());
        }
        Ok(value)
    }
    pub fn area(&self) -> Result<f64> {
        self.scalar(b"GEOSArea_r\0")
    }
    pub fn length(&self) -> Result<f64> {
        self.scalar(b"GEOSLength_r\0")
    }
    pub fn bounds(&self) -> Result<[f64; 4]> {
        let mut r = [0.; 4];
        let ok = call!(self.engine,"GEOSGeom_getExtent_r",(Ptr,Ptr,*mut f64,*mut f64,*mut f64,*mut f64)->c_int,
            self.engine.context,self.raw,&mut r[0],&mut r[1],&mut r[2],&mut r[3]);
        if ok == 0 {
            return Err("empty/invalid geometry bounds".into());
        }
        Ok(r)
    }
    pub fn kind(&self) -> Result<i32> {
        Ok(call!(self.engine,"GEOSGeomTypeId_r",(Ptr,Ptr)->c_int,self.engine.context,self.raw))
    }
    pub fn components(&self) -> Result<Vec<Self>> {
        if self.empty()? {
            return Ok(vec![]);
        }
        if self.kind()? == 3 {
            return Ok(vec![self.copy()?]);
        }
        let count = call!(self.engine,"GEOSGetNumGeometries_r",(Ptr,Ptr)->c_int,self.engine.context,self.raw);
        let mut out = vec![];
        if self.kind()? < 4 {
            return Ok(out);
        }
        for i in 0..count {
            let borrowed = call!(self.engine,"GEOSGetGeometryN_r",(Ptr,Ptr,c_int)->Ptr,self.engine.context,self.raw,i);
            let part = self.engine.own(
                call!(self.engine,"GEOSGeom_clone_r",(Ptr,Ptr)->Ptr,self.engine.context,borrowed),
            )?;
            out.extend(part.components()?);
        }
        Ok(out)
    }
    pub fn rings(&self) -> Result<Vec<Self>> {
        let exterior =
            call!(self.engine,"GEOSGetExteriorRing_r",(Ptr,Ptr)->Ptr,self.engine.context,self.raw);
        let mut rings = vec![self.engine.own(
            call!(self.engine,"GEOSGeom_clone_r",(Ptr,Ptr)->Ptr,self.engine.context,exterior),
        )?];
        let n = call!(self.engine,"GEOSGetNumInteriorRings_r",(Ptr,Ptr)->c_int,self.engine.context,self.raw);
        for i in 0..n {
            let raw = call!(self.engine,"GEOSGetInteriorRingN_r",(Ptr,Ptr,c_int)->Ptr,self.engine.context,self.raw,i);
            rings.push(self.engine.own(
                call!(self.engine,"GEOSGeom_clone_r",(Ptr,Ptr)->Ptr,self.engine.context,raw),
            )?);
        }
        Ok(rings)
    }
    pub fn points(&self) -> Result<Vec<Point>> {
        let seq =
            call!(self.engine,"GEOSGeom_getCoordSeq_r",(Ptr,Ptr)->Ptr,self.engine.context,self.raw);
        if seq.is_null() {
            return Err("geometry has no coordinate sequence".into());
        }
        let mut n = 0;
        if call!(self.engine,"GEOSCoordSeq_getSize_r",(Ptr,Ptr,*mut c_uint)->c_int,self.engine.context,seq,&mut n)
            == 0
        {
            return Err("cannot read coordinate sequence".into());
        }
        let mut out = Vec::with_capacity(n as usize);
        for i in 0..n {
            let (mut x, mut y) = (0., 0.);
            if call!(self.engine,"GEOSCoordSeq_getXY_r",(Ptr,Ptr,c_uint,*mut f64,*mut f64)->c_int,
                self.engine.context,seq,i,&mut x,&mut y)
                == 0
            {
                return Err("cannot read coordinate".into());
            }
            out.push((x, y));
        }
        if out.len() > 1 && out.first() == out.last() {
            out.pop();
        }
        Ok(out)
    }
}
