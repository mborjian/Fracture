# Backend Build Script

Run this from repo root:

```powershell
powershell -ExecutionPolicy Bypass -File apps/backend/scripts/build-backend.ps1
```

This produces:

- `apps/backend/dist/fracture-backend.exe`

The Tauri release workflow copies this artifact into `apps/desktop/src-tauri` bundle resources.
