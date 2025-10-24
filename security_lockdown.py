#!/usr/bin/env python3
"""
SINCOR Security Lockdown System
Protects critical files from unauthorized modifications
"""

import os
import hashlib
import json
import datetime
from pathlib import Path

class SecurityLockdown:
    def __init__(self, project_root="."):
        self.project_root = Path(project_root).resolve()
        self.checksum_file = self.project_root / "security_checksums.json"
        self.critical_files = [
            "app.py",
            "agency_kernel.py",
            "cortecs_core.py",
            "monetization_engine.py",
            "swarm_coordination.py",
            "memory_system.py",
            "lifecycle_system.py",
            "security_headers.py",
            "auth_system.py",
            "paypal_integration.py",
            ".env",
            "requirements.txt",
            "Dockerfile",
            "railway.json",
            ".git/hooks/pre-commit",
        ]

    def calculate_checksum(self, filepath):
        """Calculate SHA256 checksum of a file"""
        try:
            sha256_hash = hashlib.sha256()
            with open(filepath, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except FileNotFoundError:
            return None

    def create_baseline(self):
        """Create baseline checksums for all critical files"""
        checksums = {
            "created": datetime.datetime.now().isoformat(),
            "files": {}
        }

        print("Creating security baseline...")
        for file in self.critical_files:
            filepath = self.project_root / file
            checksum = self.calculate_checksum(filepath)
            if checksum:
                checksums["files"][str(file)] = {
                    "checksum": checksum,
                    "size": os.path.getsize(filepath),
                    "modified": datetime.datetime.fromtimestamp(
                        os.path.getmtime(filepath)
                    ).isoformat()
                }
                print(f"[OK] {file}: {checksum[:16]}...")
            else:
                print(f"[WARN] {file}: Not found (optional)")

        with open(self.checksum_file, "w") as f:
            json.dump(checksums, f, indent=2)

        print(f"\n[OK] Baseline saved to {self.checksum_file}")
        return checksums

    def verify_integrity(self):
        """Verify integrity of critical files against baseline"""
        if not self.checksum_file.exists():
            print("[WARN] No baseline found. Run create_baseline() first.")
            return False

        with open(self.checksum_file, "r") as f:
            baseline = json.load(f)

        print("Verifying file integrity...")
        print(f"Baseline created: {baseline['created']}\n")

        all_ok = True
        for file, data in baseline["files"].items():
            filepath = self.project_root / file
            current_checksum = self.calculate_checksum(filepath)

            if current_checksum is None:
                print(f"[ERROR] {file}: FILE MISSING!")
                all_ok = False
            elif current_checksum != data["checksum"]:
                print(f"[ERROR] {file}: CHECKSUM MISMATCH!")
                print(f"   Expected: {data['checksum'][:16]}...")
                print(f"   Got:      {current_checksum[:16]}...")
                all_ok = False
            else:
                print(f"[OK] {file}")

        return all_ok

    def lock_critical_files(self):
        """Set critical files to read-only (Windows/Unix)"""
        print("\nLocking critical files...")
        locked_count = 0

        for file in self.critical_files:
            filepath = self.project_root / file
            if filepath.exists():
                try:
                    # Set read-only on Windows and Unix
                    os.chmod(filepath, 0o444)
                    print(f"[LOCKED] {file}")
                    locked_count += 1
                except Exception as e:
                    print(f"[WARN] Could not lock {file}: {e}")

        print(f"\n[OK] Locked {locked_count} files")

    def unlock_critical_files(self):
        """Restore write permissions to critical files"""
        print("\nUnlocking critical files...")
        unlocked_count = 0

        for file in self.critical_files:
            filepath = self.project_root / file
            if filepath.exists():
                try:
                    # Restore read-write permissions
                    os.chmod(filepath, 0o644)
                    print(f"[UNLOCKED] {file}")
                    unlocked_count += 1
                except Exception as e:
                    print(f"[WARN] Could not unlock {file}: {e}")

        print(f"\n[OK] Unlocked {unlocked_count} files")

    def install_git_hooks(self):
        """Install enhanced git hooks for security"""
        hooks_dir = self.project_root / ".git" / "hooks"

        # Pre-commit hook
        pre_commit = hooks_dir / "pre-commit"
        pre_commit_content = r'''#!/usr/bin/env bash
set -euo pipefail

echo "[SECURITY] SINCOR Security Check..."

# Block HTML in mediapacks
if git diff --cached --name-only | grep -E "^mediapacks/.*\.(html?)$" >/dev/null; then
  echo "[ERROR] BLOCK: HTML forbidden under mediapacks/." >&2
  exit 1
fi

# Block banned claim language
if git diff --cached -U0 mediapacks 2>/dev/null | grep -Ei "(\$[0-9]+\s*value|ROI|guarantee|best in the world)" >/dev/null; then
  echo "[ERROR] BLOCK: Banned claim language in mediapacks." >&2
  exit 1
fi

# Verify file integrity before commit
if [ -f "security_checksums.json" ]; then
    echo "Verifying critical file integrity..."
    python3 security_lockdown.py verify || {
        echo "[ERROR] BLOCK: File integrity check failed!"
        echo "Run 'python security_lockdown.py verify' to see details"
        exit 1
    }
fi

# Block commits with secrets
if git diff --cached | grep -Ei "(password|api_key|secret|token)\s*=\s*['\"](?!.*env)" >/dev/null; then
    echo "[ERROR] BLOCK: Potential hardcoded secrets detected!"
    echo "Use environment variables instead"
    exit 1
fi

echo "[OK] Security checks passed"
exit 0
'''

        with open(pre_commit, "w", newline='\n') as f:
            f.write(pre_commit_content)
        os.chmod(pre_commit, 0o755)
        print("[OK] Installed enhanced pre-commit hook")

        # Pre-push hook
        pre_push = hooks_dir / "pre-push"
        pre_push_content = r'''#!/usr/bin/env bash
set -euo pipefail

echo "[SECURITY] Pre-push security check..."

# Prevent force push to main/master
while read local_ref local_sha remote_ref remote_sha; do
    if [[ "$remote_ref" =~ (main|master)$ ]] && git log --oneline "$remote_sha..$local_sha" | grep -q "^"; then
        if git push --dry-run 2>&1 | grep -q "forced update"; then
            echo "[ERROR] BLOCK: Force push to main/master is not allowed!"
            exit 1
        fi
    fi
done

echo "[OK] Push security check passed"
exit 0
'''

        with open(pre_push, "w", newline='\n') as f:
            f.write(pre_push_content)
        os.chmod(pre_push, 0o755)
        print("[OK] Installed pre-push hook")

if __name__ == "__main__":
    import sys

    lockdown = SecurityLockdown()

    if len(sys.argv) < 2:
        print("SINCOR Security Lockdown System")
        print("=" * 50)
        print("\nUsage:")
        print("  python security_lockdown.py baseline  - Create security baseline")
        print("  python security_lockdown.py verify    - Verify file integrity")
        print("  python security_lockdown.py lock      - Lock critical files")
        print("  python security_lockdown.py unlock    - Unlock critical files")
        print("  python security_lockdown.py hooks     - Install git security hooks")
        print("  python security_lockdown.py full      - Full lockdown (baseline + lock + hooks)")
        sys.exit(0)

    command = sys.argv[1].lower()

    if command == "baseline":
        lockdown.create_baseline()
    elif command == "verify":
        result = lockdown.verify_integrity()
        sys.exit(0 if result else 1)
    elif command == "lock":
        lockdown.lock_critical_files()
    elif command == "unlock":
        lockdown.unlock_critical_files()
    elif command == "hooks":
        lockdown.install_git_hooks()
    elif command == "full":
        print("FULL SECURITY LOCKDOWN")
        print("=" * 50)
        lockdown.create_baseline()
        print()
        lockdown.install_git_hooks()
        print()
        lockdown.lock_critical_files()
        print("\n[OK] Full lockdown complete!")
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
