use crate::{document::Layer, geos::Result, native_mesh::Mesh, planning::Geometry};
use serde_json::json;
use std::{
    fs::{File, OpenOptions},
    io::{BufWriter, Write},
    path::{Path, PathBuf},
};
pub struct Writer {
    handle: Option<BufWriter<File>>,
    pub temporary: PathBuf,
    pub destination: PathBuf,
    pub regions: usize,
    pub cells: usize,
    committed: bool,
}
impl Writer {
    pub fn open(output: &Path, step: &str) -> Result<Self> {
        std::fs::create_dir_all(output).map_err(|e| e.to_string())?;
        let destination = output.join("materialmap.json");
        let mut temporary = None;
        for serial in 0..100 {
            let path = output.join(format!(".materialmap-{}-{serial}.tmp", std::process::id()));
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(file) => {
                    temporary = Some((path, file));
                    break;
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error.to_string()),
            }
        }
        let (path, file) = temporary.ok_or("cannot allocate temporary MaterialMap")?;
        let mut writer = Self {
            handle: Some(BufWriter::new(file)),
            temporary: path,
            destination,
            regions: 0,
            cells: 0,
            committed: false,
        };
        let mut header=serde_json::to_string(&json!({"units":{"length":"mm","thermal_conductivity":"W/(m*K)","density":"kg/m3","specific_heat":"J/(kg*K)"},
            "properties":["kx","ky","kz","density","specific_heat"],"drill_model":"solid_copper","drill_scope":"dielectric_only","step":step})).map_err(|e|e.to_string())?;
        header.pop();
        writer.write(&format!("{header},\"regions\":[\n"))?;
        Ok(writer)
    }
    fn write(&mut self, text: &str) -> Result<()> {
        self.handle
            .as_mut()
            .ok_or("closed output")?
            .write_all(text.as_bytes())
            .map_err(|e| e.to_string())
    }
    pub fn layer(
        &mut self,
        layer: &Layer,
        geometry: &Geometry,
        mesh: &Mesh,
        domain: usize,
        values: &[Vec<[f64; 5]>],
    ) -> Result<()> {
        let z = layer.z_mm.as_ref().ok_or("missing absolute Z")?;
        let extent = if z.thickness_known {
            [Some(z.start_mm), Some(z.end_mm)]
        } else {
            [None, None]
        };
        for &rid in &geometry.members[domain] {
            let r = geometry.rectangles[rid];
            let (xs, ys) = &mesh.axes[rid];
            if values[rid].len() != (xs.len() - 1) * (ys.len() - 1) || values[rid].is_empty() {
                return Err("incomplete MaterialMap region".into());
            }
            if self.regions > 0 {
                self.write(",\n")?;
            }
            let region = json!({"layer":{"name":layer.name,"type":layer.kind},"bounds_mm":{"x":[r[0],r[2]],"y":[r[1],r[3]],"z":extent},
                "grid_counts":{"x":xs.len()-1,"y":ys.len()-1,"z":1},"values":values[rid]});
            serde_json::to_writer(self.handle.as_mut().unwrap(), &region)
                .map_err(|e| e.to_string())?;
            self.regions += 1;
            self.cells += values[rid].len();
        }
        Ok(())
    }
    pub fn finish(&mut self) -> Result<()> {
        if self.regions == 0 || self.cells == 0 {
            return Err("no physical material regions".into());
        }
        self.write("\n]}\n")?;
        let mut handle = self.handle.take().ok_or("closed output")?;
        handle.flush().map_err(|e| e.to_string())?;
        drop(handle);
        replace(&self.temporary, &self.destination)?;
        self.committed = true;
        Ok(())
    }
}
impl Drop for Writer {
    fn drop(&mut self) {
        self.handle.take();
        if !self.committed {
            let _ = std::fs::remove_file(&self.temporary);
        }
    }
}
pub fn replace(source: &Path, destination: &Path) -> Result<()> {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        #[link(name = "kernel32")]
        unsafe extern "system" {
            fn MoveFileExW(old: *const u16, new: *const u16, flags: u32) -> i32;
        }
        let old: Vec<_> = source.as_os_str().encode_wide().chain(Some(0)).collect();
        let new: Vec<_> = destination
            .as_os_str()
            .encode_wide()
            .chain(Some(0))
            .collect();
        if unsafe { MoveFileExW(old.as_ptr(), new.as_ptr(), 1) } == 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        Ok(())
    }
    #[cfg(not(windows))]
    {
        std::fs::rename(source, destination).map_err(|e| e.to_string())
    }
}
