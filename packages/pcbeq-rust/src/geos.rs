//! Owned GEOS geometries. All planning calls execute natively, without Shapely/Python.
use libloading::Library;
use pcb_geometry::Point;
use std::{
    cell::RefCell,
    collections::BTreeMap,
    ffi::{c_char, c_int, c_uint, c_void},
    path::Path,
    time::{Duration, Instant},
};

type Ptr = *mut c_void;
pub type Result<T> = std::result::Result<T, String>;
pub struct Geos {
    library: Library,
    context: Ptr,
    timings: RefCell<BTreeMap<&'static str, (usize, Duration)>>,
}
pub struct Shape<'a> {
    pub engine: &'a Geos,
    raw: Ptr,
}
macro_rules! call {
    ($e:expr, $name:literal, ($($ty:ty),*) -> $out:ty, $($arg:expr),*) => {{
        let started=Instant::now();
        let result=unsafe { let f = $e.library.get::<unsafe extern "C" fn($($ty),*) -> $out>(
            concat!($name,"\0").as_bytes()).map_err(|e| e.to_string())?; f($($arg),*) }
        ;$e.record($name,started.elapsed());result
    }};
}
impl Geos {
    pub fn open(path: &Path) -> Result<Self> {
        #[cfg(windows)]
        let library: Library =
            unsafe { libloading::os::windows::Library::load_with_flags(path, 8) }
                .map_err(|e| format!("cannot load GEOS {}: {e}", path.display()))?
                .into();
        #[cfg(not(windows))]
        let library = unsafe { Library::new(path) }.map_err(|e| e.to_string())?;
        let init = unsafe { library.get::<unsafe extern "C" fn() -> Ptr>(b"GEOS_init_r\0") }
            .map_err(|e| e.to_string())?;
        let context = unsafe { init() };
        if context.is_null() {
            return Err("cannot initialize GEOS".into());
        }
        Ok(Self {
            library,
            context,
            timings: RefCell::new(BTreeMap::new()),
        })
    }
    fn record(&self, name: &'static str, elapsed: Duration) {
        let mut timings = self.timings.borrow_mut();
        let entry = timings.entry(name).or_default();
        entry.0 += 1;
        entry.1 += elapsed;
    }
    pub fn timings(&self) -> serde_json::Value {
        serde_json::json!(
            self.timings
                .borrow()
                .iter()
                .map(|(name, (calls, elapsed))| (
                    (*name).to_owned(),
                    serde_json::json!({"calls":calls,"seconds":elapsed.as_secs_f64()})
                ))
                .collect::<BTreeMap<_, _>>()
        )
    }
    fn own(&self, raw: Ptr) -> Result<Shape<'_>> {
        if raw.is_null() {
            Err("native GEOS operation failed".into())
        } else {
            Ok(Shape { engine: self, raw })
        }
    }
    pub fn rectangle(&self, r: [f64; 4]) -> Result<Shape<'_>> {
        self.own(
            call!(self,"GEOSGeom_createRectangle_r",(Ptr,f64,f64,f64,f64)->Ptr,
                       self.context,r[0],r[1],r[2],r[3]),
        )
    }
    fn ring(&self, points: &[Point]) -> Result<Shape<'_>> {
        if points.len() < 3 {
            return Err("polygon needs at least three points".into());
        }
        let seq = call!(self,"GEOSCoordSeq_create_r",(Ptr,c_uint,c_uint)->Ptr,
                        self.context,(points.len()+1) as c_uint,2);
        if seq.is_null() {
            return Err("cannot allocate coordinates".into());
        }
        for (i, &(x, y)) in points.iter().chain(points.first()).enumerate() {
            let ok = call!(self,"GEOSCoordSeq_setXY_r",(Ptr,Ptr,c_uint,f64,f64)->c_int,
                           self.context,seq,i as c_uint,x,y);
            if ok == 0 {
                return Err("cannot set coordinate".into());
            }
        }
        self.own(call!(self,"GEOSGeom_createLinearRing_r",(Ptr,Ptr)->Ptr,self.context,seq))
    }
    pub fn polygon(&self, shell: &[Point], holes: &[Vec<Point>]) -> Result<Shape<'_>> {
        let outer = self.ring(shell)?;
        let inners = holes
            .iter()
            .map(|h| self.ring(h))
            .collect::<Result<Vec<_>>>()?;
        let mut raw: Vec<_> = inners.iter().map(|s| s.raw).collect();
        let ptr = call!(self,"GEOSGeom_createPolygon_r",(Ptr,Ptr,*mut Ptr,c_uint)->Ptr,
                        self.context,outer.raw,raw.as_mut_ptr(),raw.len() as c_uint);
        // GEOS takes ownership of every ring, including when construction fails.
        std::mem::forget(outer);
        for inner in inners {
            std::mem::forget(inner);
        }
        self.own(ptr)
    }
    pub fn union<'a>(&'a self, shapes: &[Shape<'a>]) -> Result<Shape<'a>> {
        let copies = shapes.iter().map(Shape::copy).collect::<Result<Vec<_>>>()?;
        let mut raw: Vec<_> = copies.iter().map(|s| s.raw).collect();
        let collection = self.own(
            call!(self,"GEOSGeom_createCollection_r",(Ptr,c_int,*mut Ptr,c_uint)->Ptr,
            self.context,7,raw.as_mut_ptr(),raw.len() as c_uint),
        )?;
        for shape in copies {
            std::mem::forget(shape);
        }
        self.own(call!(self,"GEOSUnaryUnion_r",(Ptr,Ptr)->Ptr,self.context,collection.raw))
    }
    pub fn rectangles(&self, rectangles: &[[f64; 4]]) -> Result<Shape<'_>> {
        self.union(
            &rectangles
                .iter()
                .map(|r| self.rectangle(*r))
                .collect::<Result<Vec<_>>>()?,
        )
    }
}
impl Drop for Geos {
    fn drop(&mut self) {
        unsafe {
            if let Ok(f) = self
                .library
                .get::<unsafe extern "C" fn(Ptr)>(b"GEOS_finish_r\0")
            {
                f(self.context);
            }
        }
    }
}
impl Drop for Shape<'_> {
    fn drop(&mut self) {
        unsafe {
            if let Ok(f) = self
                .engine
                .library
                .get::<unsafe extern "C" fn(Ptr, Ptr)>(b"GEOSGeom_destroy_r\0")
            {
                f(self.engine.context, self.raw);
            }
        }
    }
}
#[path = "geos_shape.rs"]
mod shape;
