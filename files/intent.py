# AccessOS — File Command Intent Parser (Phase 5)
# Maps natural language file commands to structured operation dicts.
# Used by main.py to route commands to the FileHandler without going
# through UI-TARS (file operations don't need vision reasoning).

import re
from pathlib import Path
from typing import Optional

# ── Known folder shortcuts ─────────────────────────────────────────────────
_HOME = Path.home()

KNOWN_DIRS: dict[str, Path] = {
    'downloads':  _HOME / 'Downloads',
    'download':   _HOME / 'Downloads',
    'desktop':    _HOME / 'Desktop',
    'documents':  _HOME / 'Documents',
    'document':   _HOME / 'Documents',
    'pictures':   _HOME / 'Pictures',
    'photos':     _HOME / 'Pictures',
    'music':      _HOME / 'Music',
    'videos':     _HOME / 'Videos',
    'video':      _HOME / 'Videos',
    'onedrive':   _HOME / 'OneDrive',
    'home':       _HOME,
}

# ── Detection pattern ──────────────────────────────────────────────────────
# Matches commands that involve file/folder operations.
# Deliberately conservative to avoid swallowing general commands.
_FILE_TRIGGER = re.compile(
    r'\b('
    # Action verbs
    r'find|search for|look for|where is|locate|'
    r'open|show me|go to|navigate to|'
    r'create|make|new|'
    r'rename|move|copy|'
    r'delete|remove|'
    r'read|list'
    r')\b'
    r'.{0,80}'         # up to 80 chars of context
    r'\b('
    # File/folder objects
    r'file|files|folder|directory|'
    r'document|pdf|resume|cv|report|'
    r'photo|image|picture|'
    r'download|desktop|documents|pictures|videos|music|onedrive|'
    r'\.pdf|\.txt|\.docx|\.xlsx|\.png|\.jpg|\.csv|\.zip'
    r')\b',
    re.IGNORECASE | re.DOTALL,
)


def is_file_command(text: str) -> bool:
    """
    Return True if *text* appears to be a file or document operation.

    Routes clear file operations (create folder, rename, move, copy, delete,
    read doc/pdf, find local files, open known folders/files) to the fast FileHandler.
    General GUI actions (e.g. "open Chrome", "search for AI hackathons") fall back
    to UI-TARS.
    """
    # 1. Quick regex trigger check
    if _FILE_TRIGGER.search(text):
        return True

    # 2. Check if text parses into an unambiguous file intent
    intent = parse_file_intent(text)
    if intent is None:
        return False

    op = intent.get('op')
    tl = text.lower().strip()

    # Unambiguous file operations: create_folder, rename, move, copy, delete, read_doc, read_pdf
    if op in ('create_folder', 'rename', 'move', 'copy', 'delete', 'read_doc', 'read_pdf'):
        return True

    # 'open' is safe if it resolved to a known folder or has a specific file
    if op == 'open' and (intent.get('path') or intent.get('filename')):
        return True

    # 'find' is a local file search unless it looks like a web search query
    if op == 'find':
        # Don't hijack web searches like "search for AI tutorials on google"
        web_indicators = ('google', 'youtube', 'online', 'web', 'internet', 'search for')
        if any(w in tl for w in web_indicators) and not any(ext in tl for ext in ('.pdf', '.txt', '.doc', '.xlsx', '.png', '.jpg')):
            return False
        return True

    return False


def parse_file_intent(text: str) -> Optional[dict]:
    """
    Parse a natural language file command into a structured intent dict.

    Returns:
        dict with 'op' key and operation-specific fields, or None if
        the command cannot be parsed (caller should fall back to UI-TARS).

    Supported ops:
        find, open, create_folder, rename, move, copy, delete,
        read_doc, read_pdf
    """
    t = text.strip()
    tl = t.lower()

    # ── read page N of PDF ─────────────────────────────────────────────────
    # "read page 3 of this PDF", "open page 5", "show page 2"
    m = re.search(r'\b(?:read|open|show)\s+page\s+(\d+)', tl)
    if m:
        page = int(m.group(1))
        # Try to extract a filename from the rest of the text
        fn = _extract_filename(t)
        return {'op': 'read_pdf', 'page': page, 'path': None, 'filename': fn}

    # ── create folder ──────────────────────────────────────────────────────
    # "create a folder called Hackathon"
    # "make a new folder named Reports"
    # "create folder Projects"
    m = re.search(
        r'\b(?:create|make|new)\b\s+(?:a\s+)?(?:new\s+)?(?:folder|directory)\b'
        r'(?:\s+(?:called|named|name))\s+["\u2018\u2019\u201c\u201d]?([A-Za-z0-9_\- ]+?)["\u2018\u2019\u201c\u201d]?'
        r'(?:\s+(?:in|on|at)\s+([A-Za-z0-9_\-]+))?$',
        t, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'\b(?:create|make|new)\b\s+(?:a\s+)?(?:new\s+)?(?:folder|directory)\b\s+'
            r'["\u2018\u2019\u201c\u201d]?([A-Za-z0-9_\- ]+?)["\u2018\u2019\u201c\u201d]?'
            r'(?:\s+(?:in|on|at)\s+([A-Za-z0-9_\-]+))?$',
            t, re.IGNORECASE,
        )
    if not m:
        m = re.search(
            r'\b(?:create|make|new)\b[^.]*?(?:folder|directory)\b[^.]*?'
            r'["\u2018\u2019\u201c\u201d]?([A-Za-z0-9_\- ]+)["\u2018\u2019\u201c\u201d]?',
            t, re.IGNORECASE,
        )
    if m:
        name = m.group(1).strip().rstrip('.')
        for filler in ('called', 'named', 'name'):
            name = re.sub(rf'^\b{filler}\b\s*', '', name, flags=re.IGNORECASE)
        name = name.strip()
        # Determine target directory from context
        parent = str(_HOME / 'Desktop')
        if 'documents' in tl:
            parent = str(_HOME / 'Documents')
        elif 'desktop' in tl:
            parent = str(_HOME / 'Desktop')
        full_path = str(Path(parent) / name)
        return {'op': 'create_folder', 'path': full_path, 'name': name}

    # ── open known folder ──────────────────────────────────────────────────
    # "open my Downloads folder", "show Desktop", "go to Documents"
    if re.search(r'\b(?:open|show|go to|navigate to|show me)\b', tl):
        for dir_key, dir_path in KNOWN_DIRS.items():
            if re.search(rf'\b{re.escape(dir_key)}\b', tl):
                return {'op': 'open', 'path': str(dir_path), 'filename': None}

    # ── find file ──────────────────────────────────────────────────────────
    # "find my resume", "find resume.pdf", "search for report"
    # "where is my CV", "look for the contract"
    # "find the PDF I downloaded yesterday"
    m = re.search(
        r'\b(?:find|search for|look for|where is|locate)\b\s+'
        r'(?:my\s+|the\s+|a\s+)?'
        r'["\u2018\u2019\u201c\u201d]?([A-Za-z0-9_\-\. ]+?)["\u2018\u2019\u201c\u201d]?'
        r'(?:\s+file|\s+document|\s+pdf|\s+folder)?'
        r'(?:\s+(?:that|which)\s+I)?'
        r'(?:\s+(?:downloaded|saved|created|wrote))?$',
        t, re.IGNORECASE,
    )
    if m:
        pattern = m.group(1).strip().rstrip('.')
        # Remove filler words
        for filler in ('the', 'a', 'an', 'my', 'some'):
            pattern = re.sub(rf'^\b{filler}\b\s*', '', pattern, flags=re.IGNORECASE)
        pattern = pattern.strip()

        # Determine search directory
        search_dir = None
        for dir_key, dir_path in KNOWN_DIRS.items():
            if re.search(rf'\b{re.escape(dir_key)}\b', tl):
                search_dir = str(dir_path)
                break

        # Determine recency filter
        max_age = None
        if re.search(r'\byesterday\b', tl):
            max_age = 2
        elif re.search(r'\btoday\b', tl):
            max_age = 1
        elif re.search(r'\blast\s+week\b', tl):
            max_age = 7
        elif re.search(r'\blast\s+month\b', tl):
            max_age = 31

        return {
            'op': 'find',
            'pattern': pattern,
            'search_dir': search_dir,
            'max_age_days': max_age,
        }

    # ── delete file ────────────────────────────────────────────────────────
    # "delete this file", "delete report.pdf", "remove temp.txt", "delete AccessOSTest"
    if re.search(r'\b(?:delete|remove)\b', tl):
        fn = _extract_filename(t)
        if not fn:
            # Fallback: extract target name after delete/remove
            m = re.search(
                r'\b(?:delete|remove)\b\s+(?:the\s+|this\s+)?(?:file\s+|folder\s+|directory\s+)?["\u2018\u2019\u201c\u201d]?([A-Za-z0-9_\-\. ]+?)["\u2018\u2019\u201c\u201d]?$',
                t, re.IGNORECASE,
            )
            if m:
                target = m.group(1).strip()
                if target.lower() not in ('it', 'this', 'that'):
                    fn = target
        return {
            'op': 'delete',
            'filename': fn,
            'path': None,
        }

    # ── rename file ────────────────────────────────────────────────────────
    # "rename this file to NewName.txt", "rename report to report_final", "rename test.txt to final.txt"
    m = re.search(
        r'\brename\b\s+(?:(?:this|the)\s+(?:file|folder)\s+)?(?:(?P<src>["\u2018\u2019\u201c\u201d]?[A-Za-z0-9_\-\. ]+?["\u2018\u2019\u201c\u201d]?)\s+)?\bto\b\s+["\u2018\u2019\u201c\u201d]?(?P<dst>[A-Za-z0-9_\-\. ]+?)["\u2018\u2019\u201c\u201d]?$',
        t, re.IGNORECASE,
    )
    if m:
        src = m.group('src')
        fn = src.strip().strip('"\'“”‘’') if src else None
        if not fn:
            fn = _extract_filename(t[:m.start('dst')])
        return {
            'op': 'rename',
            'new_name': m.group('dst').strip().strip('"\'“”‘’'),
            'path': None,
            'filename': fn,
        }

    # ── move file ──────────────────────────────────────────────────────────
    # "move report.pdf to Documents", "move this file to Desktop"
    if re.search(r'\bmove\b', tl):
        fn = _extract_filename(t)
        # Find destination folder from known dirs
        dst = None
        for dir_key, dir_path in KNOWN_DIRS.items():
            if re.search(rf'\bto\b.*\b{re.escape(dir_key)}\b', tl):
                dst = str(dir_path)
                break
        return {'op': 'move', 'filename': fn, 'path': None, 'dst': dst}

    # ── copy file ──────────────────────────────────────────────────────────
    # "copy report.pdf to Desktop", "copy this file to Downloads"
    if re.search(r'\bcopy\b', tl):
        fn = _extract_filename(t)
        dst = None
        for dir_key, dir_path in KNOWN_DIRS.items():
            if re.search(rf'\bto\b.*\b{re.escape(dir_key)}\b', tl):
                dst = str(dir_path)
                break
        return {'op': 'copy', 'filename': fn, 'path': None, 'dst': dst}

    # ── read document ──────────────────────────────────────────────────────
    # "read report.pdf", "read this document", "read the text file"
    if re.search(r'\bread\b', tl):
        fn = _extract_filename(t)
        if fn and fn.lower().endswith('.pdf'):
            return {'op': 'read_pdf', 'page': 1, 'path': None, 'filename': fn}
        if fn or re.search(r'\b(?:this\s+)?(?:file|document|pdf|page)\b', tl):
            return {'op': 'read_doc', 'path': None, 'filename': fn}

    # ── open specific file ─────────────────────────────────────────────────
    # "open resume.pdf", "open the file report.docx"
    if re.search(r'\b(?:open|show)\b', tl):
        fn = _extract_filename(t)
        if fn:
            return {'op': 'open', 'path': None, 'filename': fn}

    return None   # Cannot parse → caller falls back to UI-TARS


# ── Helpers ────────────────────────────────────────────────────────────────

def _extract_filename(text: str) -> Optional[str]:
    """
    Try to extract a filename (with extension) from a natural language command.

    Returns the filename string or None.
    """
    # 1. Quoted filename (supports ", ', “”, ‘’)
    m = re.search(
        r'["\'\u2018\u2019\u201c\u201d]([A-Za-z0-9_\-\. ]+\.[a-zA-Z]{2,5})["\'\u2018\u2019\u201c\u201d]',
        text,
    )
    if m:
        return m.group(1).strip()

    # 2. Filename with extension, cleanly stripping leading command verbs and prepositions
    m = re.search(r'\b([A-Za-z0-9_\-][A-Za-z0-9_\- ]*?\.[a-zA-Z]{2,5})\b', text)
    if m:
        name = m.group(1).strip()
        for prefix in (
            'read', 'delete', 'remove', 'open', 'copy', 'move',
            'this', 'the', 'file', 'document', 'pdf', 'a', 'an', 'my'
        ):
            name = re.sub(rf'^\b{prefix}\b\s*', '', name, flags=re.IGNORECASE)
        name = name.strip()
        if name:
            return name

    # 3. Standard single-word filename fallback
    m = re.search(r'\b([A-Za-z0-9_\-]+\.[a-zA-Z]{2,5})\b', text)
    if m:
        return m.group(1).strip()

    return None
