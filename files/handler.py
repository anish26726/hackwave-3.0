# AccessOS — File Handler (Phase 5)
# Safe, controlled file operations for the AccessOS agent.
#
# SECURITY MODEL:
#   - All paths validated against SAFE_ROOTS (user home + subdirs only)
#   - Path traversal (../../) is blocked by resolve() + relative_to() check
#   - System directories blocked by keyword check
#   - delete_file() must be confirmed by the caller BEFORE invocation
#   - NO subprocess, shell, eval, exec, os.system used anywhere
#
# Supported operations:
#   find_file, open_file, create_folder, rename_file,
#   move_file, copy_file, delete_file, read_document, read_pdf_page

import os
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

# ── Safe root directories ──────────────────────────────────────────────────
# File operations are restricted to the user's home directory tree.
# System directories are further blocked by _check_not_system().
_HOME = Path.home()

SAFE_ROOTS: List[Path] = [
    _HOME / 'Desktop',
    _HOME / 'Documents',
    _HOME / 'Downloads',
    _HOME / 'Pictures',
    _HOME / 'Music',
    _HOME / 'Videos',
    _HOME / 'OneDrive',
    _HOME,       # General home dir fallback — covers any subdir of home
]

# ── Limits ────────────────────────────────────────────────────────────────
MAX_FIND_RESULTS = 20       # Max files returned by find_file()
MAX_READ_CHARS   = 4000     # Max characters returned by read_document()

# ── Text file extensions supported by read_document() ─────────────────────
TEXT_EXTENSIONS = {
    '.txt', '.md', '.py', '.js', '.ts', '.html', '.htm', '.css',
    '.json', '.csv', '.log', '.xml', '.yaml', '.yml', '.ini',
    '.cfg', '.toml', '.rst', '.tex', '.bat', '.ps1', '.sh',
}


class FileOperationError(Exception):
    """Raised when a file operation fails validation or execution."""
    pass


class FileHandler:
    """
    Safe, controlled file operations for the AccessOS agent.

    All paths are resolved and checked against SAFE_ROOTS before any
    operation is performed. System directories are always blocked.
    delete_file() requires the caller to obtain user confirmation first.
    """

    # ── Path validation ───────────────────────────────────────────────────

    def _resolve_safe(self, path: str) -> Path:
        """
        Resolve *path* to an absolute Path and verify it is within SAFE_ROOTS.

        Uses Path.resolve() to eliminate .., symlinks, and relative components
        before checking against the allow-list.

        Raises:
            FileOperationError: If the resolved path is outside safe roots.
        """
        p = Path(os.path.expandvars(str(path))).expanduser().resolve()
        for root in SAFE_ROOTS:
            try:
                p.relative_to(root.resolve())
                return p          # Path is safe
            except ValueError:
                continue
        raise FileOperationError(
            f"Path '{p}' is outside allowed directories. "
            f"Allowed areas: Desktop, Documents, Downloads, Pictures, "
            f"Music, Videos, OneDrive, and home folder."
        )

    def _check_not_system(self, p: Path) -> None:
        """
        Block access to Windows system directories even if they somehow
        appear inside a user home path (e.g. junctions, symlinks).
        """
        blocked_keywords = {
            'windows', 'system32', 'syswow64', 'program files',
            'program files (x86)', 'programdata', 'winnt',
        }
        parts_lower = {part.lower() for part in p.parts}
        for kw in blocked_keywords:
            if kw in parts_lower:
                raise FileOperationError(
                    f"Operation blocked: '{p}' appears to be a system path."
                )

    def _validate_filename(self, name: str) -> None:
        """Reject filenames with path separators or Windows-reserved characters."""
        forbidden = set('/\\:*?"<>|\t\n\r\x00')
        if '..' in name:
            raise FileOperationError("Filename must not contain '..'")
        for ch in forbidden:
            if ch in name:
                raise FileOperationError(
                    f"Filename contains forbidden character: {ch!r}"
                )

    # ── Public API ────────────────────────────────────────────────────────

    def find_file(
        self,
        pattern: str,
        search_dir: Optional[str] = None,
        max_age_days: Optional[int] = None,
    ) -> List[str]:
        """
        Search for files matching *pattern* (glob) under safe directories.

        Args:
            pattern:      Filename glob, e.g. 'resume*', '*.pdf', 'report'.
                          If no wildcard or extension, wraps in *pattern*.
            search_dir:   Directory to start from (defaults to home dir).
            max_age_days: If set, only return files modified within N days.

        Returns:
            Sorted list of absolute path strings (newest-first, max MAX_FIND_RESULTS).
        """
        if not pattern or not pattern.strip():
            raise FileOperationError("Search pattern cannot be empty.")

        pat = pattern.strip()
        # Auto-wrap in wildcards if no glob and no extension present
        if '*' not in pat and '?' not in pat and '.' not in pat:
            pat = f'*{pat}*'

        if search_dir:
            root = self._resolve_safe(search_dir)
        else:
            root = _HOME

        cutoff = (
            datetime.now() - timedelta(days=max_age_days)
            if max_age_days else None
        )

        results = []
        seen = set()

        has_wildcard = '*' in pat or '?' in pat
        has_ext = '.' in pat

        # Prepare matching glob
        glob_pat = pat if (has_wildcard or has_ext) else f'*{pat}*'

        def _try_add(p: Path):
            try:
                p_res = p.resolve()
                if p_res in seen:
                    return False
                if not (p_res.is_file() or p_res.is_dir()):
                    return False
                if cutoff:
                    mtime = datetime.fromtimestamp(p_res.stat().st_mtime)
                    if mtime < cutoff:
                        return False
                seen.add(p_res)
                results.append(p_res)
                return True
            except (PermissionError, OSError):
                return False

        # Fast direct check in common user locations if exact name (no wildcards)
        if not has_wildcard:
            direct_candidates = [
                Path.cwd() / pat,
                _HOME / 'Desktop' / pat,
                _HOME / 'Documents' / pat,
                _HOME / 'Downloads' / pat,
                _HOME / pat,
            ]
            for cand in direct_candidates:
                if cand.exists():
                    _try_add(cand)

        # If search_dir is provided, search only there
        if search_dir:
            search_roots = [self._resolve_safe(search_dir)]
        else:
            # Search primary user directories
            search_roots = [
                Path.cwd(),
                _HOME / 'Desktop',
                _HOME / 'Documents',
                _HOME / 'Downloads',
                _HOME / 'Pictures',
                _HOME / 'Videos',
                _HOME / 'Music',
            ]
            onedrive = _HOME / 'OneDrive'
            if onedrive.exists():
                search_roots.append(onedrive)

        import fnmatch
        for s_root in search_roots:
            if len(results) >= MAX_FIND_RESULTS:
                break
            if not s_root.exists():
                continue
            for root_dir, dirs, files in os.walk(str(s_root), topdown=True, followlinks=False):
                if len(results) >= MAX_FIND_RESULTS:
                    break
                # Prune out hidden and heavy directories to prevent slowdowns & permission crashes
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith('.')
                    and d.lower() not in (
                        'appdata', 'application data', 'node_modules', '$recycle.bin',
                        'system volume information', '__pycache__', '.venv', '.git'
                    )
                ]
                for item_name in files + dirs:
                    if fnmatch.fnmatch(item_name.lower(), glob_pat.lower()):
                        item_path = Path(root_dir) / item_name
                        _try_add(item_path)
                        if len(results) >= MAX_FIND_RESULTS:
                            break

        # Sort newest-first for relevance
        results.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [str(p) for p in results]

    def open_file(self, path: str) -> str:
        """
        Open a file or folder using Windows' default application.
        Uses os.startfile — no shell commands.

        Args:
            path: Path to file or folder.

        Returns:
            Status message.
        """
        p = self._resolve_safe(path)
        self._check_not_system(p)

        if not p.exists():
            raise FileOperationError(f"Not found: '{p}'")

        try:
            os.startfile(str(p))
            kind = "folder" if p.is_dir() else "file"
            return f"Opened {kind}: {p.name}"
        except Exception as e:
            raise FileOperationError(f"Could not open '{p.name}': {e}")

    def create_folder(self, path: str) -> str:
        """
        Create a new folder (and any necessary parent folders).

        Args:
            path: Full path of the folder to create.

        Returns:
            Status message.
        """
        p = self._resolve_safe(path)
        self._check_not_system(p)
        self._validate_filename(p.name)

        if p.exists():
            if p.is_dir():
                return f"Folder already exists: {p}"
            raise FileOperationError(f"A file named '{p.name}' already exists at that location.")

        try:
            p.mkdir(parents=True, exist_ok=True)
            return f"Created folder: {p}"
        except Exception as e:
            raise FileOperationError(f"Could not create folder '{p.name}': {e}")

    def rename_file(self, path: str, new_name: str) -> str:
        """
        Rename a file or folder.

        Args:
            path:     Existing file or folder path.
            new_name: New name (filename only — no path separators).

        Returns:
            Status message.
        """
        p = self._resolve_safe(path)
        self._check_not_system(p)
        self._validate_filename(new_name)

        if not p.exists():
            raise FileOperationError(f"Not found: '{p}'")

        new_path = p.parent / new_name
        if new_path.exists():
            raise FileOperationError(
                f"A file or folder named '{new_name}' already exists here. "
                "Choose a different name."
            )

        try:
            p.rename(new_path)
            return f"Renamed '{p.name}' → '{new_name}'"
        except Exception as e:
            raise FileOperationError(f"Could not rename '{p.name}': {e}")

    def move_file(self, src: str, dst: str) -> str:
        """
        Move a file or folder to a new location.

        Args:
            src: Source path (file or folder).
            dst: Destination path (directory or new file path).

        Returns:
            Status message.
        """
        s = self._resolve_safe(src)
        d = self._resolve_safe(dst)
        self._check_not_system(s)
        self._check_not_system(d)

        if not s.exists():
            raise FileOperationError(f"Source not found: '{s}'")

        # If dst is an existing dir, check for collision
        if d.is_dir():
            collision = d / s.name
            if collision.exists():
                raise FileOperationError(
                    f"'{s.name}' already exists in the destination folder. "
                    "Delete or rename it first."
                )

        try:
            shutil.move(str(s), str(d))
            dest_display = d / s.name if d.is_dir() else d
            return f"Moved '{s.name}' → '{dest_display}'"
        except Exception as e:
            raise FileOperationError(f"Could not move '{s.name}': {e}")

    def copy_file(self, src: str, dst: str) -> str:
        """
        Copy a file to a new location.

        Args:
            src: Source file path.
            dst: Destination directory or file path.

        Returns:
            Status message.
        """
        s = self._resolve_safe(src)
        d = self._resolve_safe(dst)
        self._check_not_system(s)

        if not s.exists():
            raise FileOperationError(f"Source not found: '{s}'")
        if not s.is_file():
            raise FileOperationError(f"'{s.name}' is not a file. Only files can be copied.")

        # Resolve destination
        if d.is_dir():
            dest_file = d / s.name
        else:
            dest_file = d

        if dest_file.exists():
            raise FileOperationError(
                f"'{dest_file.name}' already exists at the destination. "
                "Delete or rename it first."
            )

        try:
            shutil.copy2(str(s), str(dest_file))
            return f"Copied '{s.name}' → '{dest_file}'"
        except Exception as e:
            raise FileOperationError(f"Could not copy '{s.name}': {e}")

    def delete_file(self, path: str) -> str:
        """
        Permanently delete a file or empty folder.

        ⚠️  IMPORTANT: The caller MUST obtain explicit user confirmation
        before calling this method. The run_file_command() function in
        main.py handles confirmation for text/voice mode.

        Args:
            path: Path to delete.

        Returns:
            Status message.
        """
        p = self._resolve_safe(path)
        self._check_not_system(p)

        if not p.exists():
            raise FileOperationError(f"Not found: '{p}'")

        try:
            if p.is_file():
                p.unlink()
                return f"Deleted file: {p.name}"
            elif p.is_dir():
                # Only delete if empty — no recursive deletion without extra confirmation
                contents = list(p.iterdir())
                if contents:
                    count = len(contents)
                    raise FileOperationError(
                        f"'{p.name}' is not empty ({count} item(s) inside). "
                        "Delete the contents first, or specify a file directly."
                    )
                p.rmdir()
                return f"Deleted folder: {p.name}"
            else:
                raise FileOperationError(f"'{p.name}' is not a regular file or folder.")
        except FileOperationError:
            raise
        except PermissionError:
            raise FileOperationError(
                f"Permission denied — '{p.name}' is locked or in use by another program."
            )
        except Exception as e:
            raise FileOperationError(f"Could not delete '{p.name}': {e}")

    def read_document(self, path: str, max_chars: int = MAX_READ_CHARS) -> str:
        """
        Read and return the text content of a file.

        Supports: text files (.txt, .md, .py, .json, .csv …) and .pdf.

        Args:
            path:      Path to the document.
            max_chars: Maximum characters to return (prevents huge dumps).

        Returns:
            Document text.
        """
        p = self._resolve_safe(path)
        if not p.exists():
            raise FileOperationError(f"File not found: '{p}'")
        if not p.is_file():
            raise FileOperationError(f"'{p.name}' is a folder, not a file.")

        suffix = p.suffix.lower()

        if suffix == '.pdf':
            return self.read_pdf_page(path, page=1)

        if suffix not in TEXT_EXTENSIONS:
            raise FileOperationError(
                f"Unsupported file type '{suffix}'. "
                f"Readable types: .pdf, .txt, .md, .json, .csv, .py, .html and more."
            )

        for encoding in ('utf-8', 'utf-8-sig', 'latin-1', 'cp1252'):
            try:
                text = p.read_text(encoding=encoding)
                header = f"[{p.name}]\n\n"
                if len(text) > max_chars:
                    text = text[:max_chars]
                    footer = f"\n\n[… file truncated at {max_chars} characters]"
                    return header + text + footer
                return header + text
            except UnicodeDecodeError:
                continue
        raise FileOperationError(
            f"Cannot decode '{p.name}'. The file may be binary or use an unsupported encoding."
        )

    def read_pdf_page(self, path: str, page: int = 1) -> str:
        """
        Extract text from a specific page of a PDF.

        Uses pypdf (pure Python). Falls back to a helpful error if pypdf
        is not installed or the PDF is encrypted.

        Args:
            path: Path to the PDF file.
            page: 1-based page number (default: 1).

        Returns:
            Extracted text from the page, with a header showing file/page info.
        """
        p = self._resolve_safe(path)
        if not p.exists():
            raise FileOperationError(f"File not found: '{p}'")
        if p.suffix.lower() != '.pdf':
            raise FileOperationError(
                f"'{p.name}' is not a PDF. Use read_document() for text files."
            )

        try:
            import pypdf
        except ImportError:
            raise FileOperationError(
                "pypdf is not installed. Run: pip install pypdf\n"
                "Then try again."
            )

        try:
            reader = pypdf.PdfReader(str(p))

            if reader.is_encrypted:
                raise FileOperationError(
                    f"'{p.name}' is password-protected and cannot be read."
                )

            total_pages = len(reader.pages)
            if page < 1 or page > total_pages:
                raise FileOperationError(
                    f"Page {page} does not exist. "
                    f"'{p.name}' has {total_pages} page(s). "
                    f"Request a page between 1 and {total_pages}."
                )

            page_obj = reader.pages[page - 1]
            text = (page_obj.extract_text() or "").strip()

            header = f"[{p.name} — Page {page} of {total_pages}]\n\n"

            if not text:
                return (
                    header
                    + "(No extractable text on this page. "
                    "It may be a scanned image. "
                    "Try opening the file and using 'read my screen' instead.)"
                )

            if len(text) > MAX_READ_CHARS:
                text = text[:MAX_READ_CHARS] + "\n\n[… page truncated]"

            return header + text

        except FileOperationError:
            raise
        except Exception as e:
            raise FileOperationError(f"Could not read PDF '{p.name}': {e}")


# ── Module-level singleton ─────────────────────────────────────────────────

_handler: Optional[FileHandler] = None


def get_handler() -> FileHandler:
    global _handler
    if _handler is None:
        _handler = FileHandler()
    return _handler
