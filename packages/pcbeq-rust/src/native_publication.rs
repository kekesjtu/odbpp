//! Transactional numerical publication and removal of previously owned pictures.
use crate::geos::Result;
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs,
    path::{Component, Path, PathBuf},
};

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
    pub fn commit(&self) -> Result<()> {
        let files = ["materialmap.json", "diagnostics/run.json"];
        let manifest = "diagnostics/artifacts.json";
        let mut names = files.iter().map(|s| s.to_string()).collect::<BTreeSet<_>>();
        names.insert(manifest.into());
        for name in [manifest, "diagnostics/image-artifacts.json"] {
            let path = self.directory.join(name);
            if path.exists() {
                let value: Value =
                    serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
                        .map_err(|e| format!("invalid artifact manifest: {e}"))?;
                let previous = value["files"]
                    .as_array()
                    .ok_or("invalid artifact manifest files")?;
                for name in previous {
                    names.insert(name.as_str().ok_or("invalid artifact filename")?.to_owned());
                }
                names.insert(name.into());
            }
        }
        for name in &names {
            let relative = Path::new(name);
            if relative.as_os_str().is_empty()
                || !relative
                    .components()
                    .all(|c| matches!(c, Component::Normal(_)))
            {
                return Err(format!("unsafe output manifest path: {name}"));
            }
            let mut target = self.directory.clone();
            for part in relative.components() {
                target.push(part);
                if let Ok(meta) = fs::symlink_metadata(&target) {
                    if meta.is_symlink() {
                        return Err(format!("symlink in output path: {}", target.display()));
                    }
                }
            }
            if target.exists() && !target.is_file() {
                return Err(format!("output destination is not a regular file: {name}"));
            }
        }
        fs::write(
            self.path.join(manifest),
            serde_json::to_vec_pretty(&json!({"files":files})).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        let backup = self.temporary.join("old");
        let mut moved = vec![];
        let mut installed = vec![];
        let result = (|| -> Result<()> {
            for name in &names {
                let target = self.directory.join(name);
                if target.exists() {
                    let saved = backup.join(name);
                    fs::create_dir_all(saved.parent().unwrap()).map_err(|e| e.to_string())?;
                    fs::rename(&target, saved).map_err(|e| e.to_string())?;
                    moved.push(name);
                }
                let source = self.path.join(name);
                if source.is_file() {
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
