//! Transactional publication of fixed product files and dedicated image directories.
use crate::Result;
use std::{
    fs,
    path::{Path, PathBuf},
};

pub const OUTPUTS: [&str; 6] = [
    "regions",
    "grids",
    "heatmaps",
    "index.html",
    "materialmap.json",
    "diagnostics.json",
];

pub struct Publication {
    pub path: PathBuf,
    temporary: PathBuf,
    directory: PathBuf,
    parent: PathBuf,
    preserve: std::cell::Cell<bool>,
}
impl Publication {
    pub fn new(directory: &Path) -> Result<Self> {
        fs::create_dir_all(directory).map_err(|e| e.to_string())?;
        let directory = directory.canonicalize().map_err(|e| e.to_string())?;
        let parent = directory
            .parent()
            .ok_or("output has no parent")?
            .to_path_buf();
        let mut temporary = None;
        for serial in 0..1000 {
            let path = parent.join(format!(".pcbeq-native-{}-{serial}", std::process::id()));
            match fs::create_dir(&path) {
                Ok(()) => {
                    temporary = Some(path);
                    break;
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e.to_string()),
            }
        }
        let temporary = temporary.ok_or("cannot allocate output transaction")?;
        let path = temporary.join("new");
        fs::create_dir(&path).map_err(|e| e.to_string())?;
        Ok(Self {
            path,
            temporary,
            directory,
            parent,
            preserve: std::cell::Cell::new(false),
        })
    }
    pub fn commit(&self, finish_diagnostics: impl FnOnce() -> Result<()>) -> Result<()> {
        for (index, name) in OUTPUTS.iter().enumerate() {
            let target = self.directory.join(name);
            if let Ok(meta) = fs::symlink_metadata(&target) {
                if meta.is_symlink()
                    || (index < 3 && !meta.is_dir())
                    || (index >= 3 && !meta.is_file())
                {
                    return Err(format!("invalid output destination: {}", target.display()));
                }
            }
        }
        let backup = self.temporary.join("old");
        let mut moved = vec![];
        let mut installed = vec![];
        let result = (|| -> Result<()> {
            let mut finish_diagnostics = Some(finish_diagnostics);
            for name in OUTPUTS {
                if name == "diagnostics.json" {
                    // MaterialMap is installed; stop numerical timing before serializing diagnostics.
                    finish_diagnostics.take().unwrap()()?;
                }
                let target = self.directory.join(name);
                if target.exists() {
                    let saved = backup.join(name);
                    fs::create_dir_all(saved.parent().unwrap()).map_err(|e| e.to_string())?;
                    fs::rename(&target, saved).map_err(|e| e.to_string())?;
                    moved.push(name);
                }
                let source = self.path.join(name);
                if source.exists() {
                    fs::create_dir_all(target.parent().unwrap()).map_err(|e| e.to_string())?;
                    fs::rename(source, target).map_err(|e| e.to_string())?;
                    installed.push(name);
                }
            }
            Ok(())
        })();
        if let Err(error) = result {
            for name in installed.iter().rev() {
                let _ = fs::remove_file(self.directory.join(name));
            }
            let mut rollback = vec![];
            for name in moved.iter().rev() {
                if let Err(e) = fs::rename(backup.join(name), self.directory.join(name)) {
                    rollback.push(e.to_string());
                }
            }
            if !rollback.is_empty() {
                self.preserve.set(true);
                return Err(format!(
                    "publication failed: {error}; rollback failed: {rollback:?}; backup={}",
                    backup.display()
                ));
            }
            return Err(error);
        }
        Ok(())
    }
}
impl Drop for Publication {
    fn drop(&mut self) {
        // Remove only our uniquely created transaction under its verified absolute parent.
        if !self.preserve.get()
            && self
                .temporary
                .canonicalize()
                .ok()
                .is_some_and(|path| path.parent() == Some(self.parent.as_path()))
        {
            let _ = fs::remove_dir_all(&self.temporary);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn diagnostic_failure_rolls_back_materialmap_and_image_directories() {
        let serial = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root =
            std::env::temp_dir().join(format!("pcbeq-publication-{}-{serial}", std::process::id()));
        let directory = root.join("out");
        fs::create_dir_all(directory.join("regions")).unwrap();
        fs::write(directory.join("materialmap.json"), "previous map").unwrap();
        fs::write(directory.join("diagnostics.json"), "previous diagnostics").unwrap();
        fs::write(directory.join("regions/region.png"), "previous image").unwrap();
        let publication = Publication::new(&directory).unwrap();
        fs::write(publication.path.join("materialmap.json"), "new map").unwrap();
        let result = publication.commit(|| Err("diagnostic serialization failed".into()));
        assert!(
            result
                .unwrap_err()
                .contains("diagnostic serialization failed")
        );
        assert_eq!(
            fs::read_to_string(directory.join("materialmap.json")).unwrap(),
            "previous map"
        );
        assert_eq!(
            fs::read_to_string(directory.join("diagnostics.json")).unwrap(),
            "previous diagnostics"
        );
        assert_eq!(
            fs::read_to_string(directory.join("regions/region.png")).unwrap(),
            "previous image"
        );
        drop(publication);
        assert_eq!(
            root.canonicalize().unwrap().parent(),
            Some(std::env::temp_dir().canonicalize().unwrap().as_path())
        );
        fs::remove_dir_all(root).unwrap();
    }
}
