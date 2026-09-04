# -*- coding: utf-8 -*-
"""Phase 5 smoke test -- validates all Phase 5 file operations and intent parsing."""
import sys
import shutil
from pathlib import Path

errors = []

def ok(msg):
    print("  [PASS] " + str(msg))

def fail(msg, e):
    print("  [FAIL] " + str(msg) + ": " + str(e))
    errors.append(msg)

print("AccessOS Phase 5 -- File Operations Smoke Test")
print("================================================")

# 1. Module Imports
try:
    from files.handler import FileHandler, FileOperationError, get_handler, SAFE_ROOTS
    from files.intent import is_file_command, parse_file_intent, KNOWN_DIRS
    ok("modules imported successfully")
except Exception as e:
    fail("module imports", e)
    sys.exit(1)

# 2. Intent Parsing Tests
# 2.1 find
try:
    assert is_file_command("find my resume"), "is_file_command('find my resume') was False"
    intent = parse_file_intent("find resume.pdf")
    assert intent and intent["op"] == "find" and "resume" in intent["pattern"], f"Got {intent}"
    ok("intent: 'find resume.pdf' -> op='find'")
except Exception as e:
    fail("intent find", e)

# 2.2 open known folder
try:
    assert is_file_command("open my Downloads folder"), "is_file_command('open my Downloads folder') was False"
    intent = parse_file_intent("open my Downloads folder")
    assert intent and intent["op"] == "open" and "Downloads" in intent["path"], f"Got {intent}"
    ok("intent: 'open my Downloads folder' -> op='open'")
except Exception as e:
    fail("intent open folder", e)

# 2.3 create folder
try:
    assert is_file_command("create a folder called TestAccessOS"), "is_file_command was False"
    intent = parse_file_intent("create a folder called TestAccessOS")
    assert intent and intent["op"] == "create_folder" and intent["name"] == "TestAccessOS", f"Got {intent}"
    ok("intent: 'create a folder called TestAccessOS' -> op='create_folder'")
except Exception as e:
    fail("intent create_folder", e)

# 2.4 rename
try:
    assert is_file_command("rename test.txt to final.txt"), "is_file_command was False"
    intent = parse_file_intent("rename test.txt to final.txt")
    assert intent and intent["op"] == "rename" and intent["new_name"] == "final.txt", f"Got {intent}"
    ok("intent: 'rename test.txt to final.txt' -> op='rename'")
except Exception as e:
    fail("intent rename", e)

# 2.5 move
try:
    assert is_file_command("move report.pdf to Documents"), "is_file_command was False"
    intent = parse_file_intent("move report.pdf to Documents")
    assert intent and intent["op"] == "move" and intent["dst"], f"Got {intent}"
    ok("intent: 'move report.pdf to Documents' -> op='move'")
except Exception as e:
    fail("intent move", e)

# 2.6 copy
try:
    assert is_file_command("copy report.pdf to Desktop"), "is_file_command was False"
    intent = parse_file_intent("copy report.pdf to Desktop")
    assert intent and intent["op"] == "copy" and intent["dst"], f"Got {intent}"
    ok("intent: 'copy report.pdf to Desktop' -> op='copy'")
except Exception as e:
    fail("intent copy", e)

# 2.7 read document
try:
    assert is_file_command("read notes.txt"), "is_file_command was False"
    intent = parse_file_intent("read notes.txt")
    assert intent and intent["op"] == "read_doc" and intent["filename"] == "notes.txt", f"Got {intent}"
    ok("intent: 'read notes.txt' -> op='read_doc'")
except Exception as e:
    fail("intent read_doc", e)

# 2.8 read pdf
try:
    assert is_file_command("read page 2 of document.pdf"), "is_file_command was False"
    intent = parse_file_intent("read page 2 of document.pdf")
    assert intent and intent["op"] == "read_pdf" and intent["page"] == 2, f"Got {intent}"
    ok("intent: 'read page 2 of document.pdf' -> op='read_pdf'")
except Exception as e:
    fail("intent read_pdf", e)

# 2.9 delete
try:
    assert is_file_command("delete old_notes.txt"), "is_file_command was False"
    intent = parse_file_intent("delete old_notes.txt")
    assert intent and intent["op"] == "delete" and intent["filename"] == "old_notes.txt", f"Got {intent}"
    ok("intent: 'delete old_notes.txt' -> op='delete'")
except Exception as e:
    fail("intent delete", e)

# 3. Security & Safe Root Restrictions
try:
    handler = get_handler()
    # Path traversal block
    try:
        handler._resolve_safe("C:/Windows/System32/calc.exe")
        fail("security", "Expected system path to be blocked")
    except FileOperationError:
        ok("security: System path C:/Windows blocked properly")

    try:
        handler._validate_filename("test/file.txt")
        fail("security", "Expected path separator in filename to be rejected")
    except FileOperationError:
        ok("security: Path separator in filename rejected properly")
except Exception as e:
    fail("security validation", e)

# 4. End-to-End File Operations (Sandbox in User Desktop/test_accessos_sandbox)
sandbox = Path.home() / "Desktop" / "_accessos_test_sandbox"
try:
    if sandbox.exists():
        shutil.rmtree(sandbox)

    # 4.1 Create folder
    res = handler.create_folder(str(sandbox))
    assert sandbox.exists() and sandbox.is_dir()
    ok(f"create_folder: created sandbox directory ({res})")

    # 4.2 Create a dummy text file
    test_file = sandbox / "sample.txt"
    test_file.write_text("Hello AccessOS Phase 5!", encoding="utf-8")
    assert test_file.exists()
    ok("sandbox: created sample.txt")

    # 4.3 Find file
    found = handler.find_file("sample.txt", search_dir=str(sandbox))
    assert len(found) > 0 and str(test_file) in found
    ok(f"find_file: located sample.txt in {sandbox.name}")

    # 4.4 Read document
    content = handler.read_document(str(test_file))
    assert "Hello AccessOS Phase 5!" in content
    ok("read_document: extracted content correctly")

    # 4.5 Copy file
    copied_file = sandbox / "sample_copy.txt"
    res = handler.copy_file(str(test_file), str(copied_file))
    assert copied_file.exists()
    ok(f"copy_file: copied sample.txt -> sample_copy.txt")

    # 4.6 Rename file
    renamed_file = sandbox / "sample_renamed.txt"
    res = handler.rename_file(str(copied_file), "sample_renamed.txt")
    assert renamed_file.exists() and not copied_file.exists()
    ok(f"rename_file: renamed to sample_renamed.txt")

    # 4.7 Move file (into a subfolder)
    subfolder = sandbox / "subfolder"
    handler.create_folder(str(subfolder))
    res = handler.move_file(str(renamed_file), str(subfolder))
    assert (subfolder / "sample_renamed.txt").exists()
    ok("move_file: moved file into subfolder")

    # 4.8 Delete file
    res = handler.delete_file(str(subfolder / "sample_renamed.txt"))
    assert not (subfolder / "sample_renamed.txt").exists()
    ok("delete_file: deleted single file")

    # Clean up sandbox
    handler.delete_file(str(subfolder))
    handler.delete_file(str(test_file))
    handler.delete_file(str(sandbox))
    assert not sandbox.exists()
    ok("cleanup: sandbox deleted safely")

except Exception as e:
    fail("end-to-end file operations", e)
    # Ensure cleanup
    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)

print("================================================")
if not errors:
    print("All Phase 5 file operations PASSED without errors!")
else:
    print(f"Failed {len(errors)} check(s): {errors}")
