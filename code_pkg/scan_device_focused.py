#!/usr/bin/env python3
"""
Phase 1 - Focused SINCOR Discovery Scanner (Optimized)
Prioritizes SINCOR-named directories, then does broader scan
"""

import os
import sys
import hashlib
import json
import csv
from pathlib import Path
from datetime import datetime
import time

# High-signal keywords
KEYWORDS = [
    'sincor', 'sincor2', 'clinton', 'detailing', 'auto_detail',
    'booking', 'agents', 'observability', 'analytics', 'monetization',
    'revenue', 'flyer', 'canva', 'promo', 'calendar', 'google',
    'compliance', 'jwt', 'limiter', 'security', 'dashboard',
    'archetype', 'agent_analytics', 'agent_interaction', 'almanak'
]

# SINCOR directory patterns (case-insensitive)
SINCOR_DIR_PATTERNS = [
    'sincor', 'sincor2', 'sincor-clean', 'sincor-fresh',
    'sincor_', 'sincor33', 'clinton'
]

EXTENSIONS_OF_INTEREST = {
    '.py', '.js', '.ts', '.tsx', '.json', '.yml', '.yaml', '.md', '.rst',
    '.ini', '.cfg', '.toml', '.sql', '.ps1', '.bat', '.sh', '.html', '.css',
    '.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp',
    '.pdf', '.docx', '.pptx', '.xlsx', '.csv', '.ipynb',
    '.zip', '.7z', '.rar'
}

EXCLUDE_DIRS = {
    'node_modules', '.git', '.cache', '__pycache__', '.venv', 'venv',
    '.mypy_cache', 'dist', 'build', '.parcel-cache', 'DerivedData',
    'tmp', 'Temp', '.vs', 'bin', 'obj', '$RECYCLE.BIN', 'System Volume Information',
    'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
    'AppData', '.npm', '.nuget'
}

class FocusedScanner:
    def __init__(self, roots, staging_dir):
        self.roots = roots
        self.staging_dir = Path(staging_dir)
        self.reports_dir = self.staging_dir / 'reports'
        self.logs_dir = self.staging_dir / 'logs'
        self.manifest = []
        self.sincor_dirs_found = []
        self.stats = {
            'total_scanned': 0,
            'matched': 0,
            'skipped': 0,
            'errors': 0,
            'sincor_dirs': 0
        }

    def is_sincor_directory(self, dirname):
        """Check if directory name contains SINCOR patterns"""
        dirname_lower = dirname.lower()
        return any(pattern in dirname_lower for pattern in SINCOR_DIR_PATTERNS)

    def calculate_topic_score(self, path_str):
        """Score file based on keyword matches"""
        path_lower = path_str.lower()
        score = 0
        for keyword in KEYWORDS:
            if keyword in path_lower:
                if keyword in Path(path_str).name.lower():
                    score += 10
                else:
                    score += 1
        return score

    def calculate_sha256(self, filepath, max_size=500*1024*1024):
        """Calculate SHA256 hash"""
        try:
            size = os.path.getsize(filepath)
            if size > max_size:
                return 'SKIPPED_TOO_LARGE'

            sha256 = hashlib.sha256()
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            return f'ERROR:{str(e)[:50]}'

    def should_skip_dir(self, dirname):
        """Check if directory should be excluded"""
        return dirname in EXCLUDE_DIRS

    def find_sincor_directories(self, root):
        """Find all SINCOR-named directories within a root (shallow scan)"""
        sincor_dirs = []
        root_path = Path(root)

        if not root_path.exists():
            return sincor_dirs

        print(f"[DISCOVERING SINCOR DIRS] {root}")

        try:
            # Scan up to 3 levels deep for SINCOR directories
            for dirpath, dirnames, _ in os.walk(root):
                depth = dirpath[len(str(root)):].count(os.sep)
                if depth > 3:
                    dirnames[:] = []  # Don't go deeper
                    continue

                # Remove excluded directories
                dirnames[:] = [d for d in dirnames if not self.should_skip_dir(d)]

                # Check for SINCOR directories
                for dirname in dirnames[:]:
                    if self.is_sincor_directory(dirname):
                        full_path = Path(dirpath) / dirname
                        sincor_dirs.append(str(full_path))
                        print(f"  [FOUND] {full_path}")
                        # Don't descend into found SINCOR dirs here
                        dirnames.remove(dirname)

        except Exception as e:
            print(f"  [ERROR] discovering in {root}: {e}")

        return sincor_dirs

    def scan_directory_full(self, directory):
        """Full recursive scan of a directory"""
        print(f"\n[SCANNING FULL] {directory}")

        for dirpath, dirnames, filenames in os.walk(directory):
            # Filter excluded directories
            dirnames[:] = [d for d in dirnames if not self.should_skip_dir(d)]

            for filename in filenames:
                self.stats['total_scanned'] += 1

                if self.stats['total_scanned'] % 500 == 0:
                    print(f"  Progress: {self.stats['total_scanned']} files, {self.stats['matched']} matched")

                filepath = Path(dirpath) / filename

                try:
                    ext = filepath.suffix.lower()
                    if ext not in EXTENSIONS_OF_INTEREST:
                        self.stats['skipped'] += 1
                        continue

                    # All files in SINCOR directories are relevant
                    topic_score = self.calculate_topic_score(str(filepath))

                    # Get file stats
                    stat = filepath.stat()
                    mtime = datetime.fromtimestamp(stat.st_mtime)
                    sha256 = self.calculate_sha256(str(filepath))

                    entry = {
                        'path': str(filepath),
                        'size': stat.st_size,
                        'mtime_iso': mtime.isoformat(),
                        'mtime_epoch': int(stat.st_mtime),
                        'sha256': sha256,
                        'topic_score': topic_score,
                        'ext': ext
                    }

                    self.manifest.append(entry)
                    self.stats['matched'] += 1

                except Exception as e:
                    self.stats['errors'] += 1
                    if self.stats['errors'] <= 10:
                        print(f"  [ERROR] {filepath}: {e}")

    def write_sincor_dirs(self):
        """Write list of discovered SINCOR directories"""
        output_file = self.reports_dir / 'sincor_directories.txt'
        with open(output_file, 'w', encoding='utf-8') as f:
            for d in self.sincor_dirs_found:
                f.write(d + '\n')
        print(f"[WRITTEN] {output_file} ({len(self.sincor_dirs_found)} directories)")

    def write_manifest_raw(self):
        """Write raw JSONL manifest"""
        output_file = self.reports_dir / 'manifest_raw.jsonl'
        with open(output_file, 'w', encoding='utf-8') as f:
            for entry in self.manifest:
                f.write(json.dumps(entry) + '\n')
        print(f"[WRITTEN] {output_file} ({len(self.manifest)} entries)")

    def write_manifest_preview(self):
        """Write CSV preview"""
        output_file = self.reports_dir / 'manifest_preview.csv'
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['path', 'sizeMB', 'sha12', 'topicScore', 'mtime'])
            for entry in self.manifest:
                writer.writerow([
                    entry['path'],
                    round(entry['size'] / (1024*1024), 2),
                    entry['sha256'][:12] if len(entry['sha256']) >= 12 else entry['sha256'],
                    entry['topic_score'],
                    entry['mtime_iso']
                ])
        print(f"[WRITTEN] {output_file}")

    def write_top200(self):
        """Write top 200 files"""
        sorted_manifest = sorted(
            self.manifest,
            key=lambda x: (x['topic_score'], x['mtime_epoch']),
            reverse=True
        )[:200]

        output_file = self.reports_dir / 'top200.csv'
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['rank', 'path', 'sizeMB', 'topicScore', 'mtime', 'ext'])
            for i, entry in enumerate(sorted_manifest, 1):
                writer.writerow([
                    i,
                    entry['path'],
                    round(entry['size'] / (1024*1024), 2),
                    entry['topic_score'],
                    entry['mtime_iso'],
                    entry['ext']
                ])
        print(f"[WRITTEN] {output_file}")

    def print_stats(self):
        """Print summary"""
        print(f"\n{'='*60}")
        print("PHASE 1 COMPLETE - Focused SINCOR Discovery")
        print(f"{'='*60}")
        print(f"SINCOR directories found: {len(self.sincor_dirs_found)}")
        print(f"Total files scanned: {self.stats['total_scanned']:,}")
        print(f"Matched files: {self.stats['matched']:,}")
        print(f"Skipped files: {self.stats['skipped']:,}")
        print(f"Errors: {self.stats['errors']:,}")
        print(f"\nManifest entries: {len(self.manifest):,}")
        total_gb = sum(e['size'] for e in self.manifest) / (1024**3)
        print(f"Total size: {total_gb:.2f} GB")
        print(f"{'='*60}")

def main():
    staging_dir = Path(r'C:\_sincor_consolidation')
    roots_file = staging_dir / 'reports' / 'roots.txt'

    if not roots_file.exists():
        print(f"[ERROR] roots.txt not found")
        sys.exit(1)

    with open(roots_file, 'r', encoding='utf-8') as f:
        roots = [line.strip().strip('\ufeff') for line in f if line.strip()]

    print("="*60)
    print("PHASE 1 - Focused SINCOR Discovery (Optimized)")
    print("="*60)
    print("Strategy: Find SINCOR directories first, then scan them fully")
    print(f"Search roots: {len(roots)}")
    print("="*60)

    scanner = FocusedScanner(roots, staging_dir)
    start_time = time.time()

    # STEP 1: Discover all SINCOR directories
    print("\n=== STEP 1: DISCOVERING SINCOR DIRECTORIES ===")
    for root in roots:
        sincor_dirs = scanner.find_sincor_directories(root)
        scanner.sincor_dirs_found.extend(sincor_dirs)

    print(f"\n[DISCOVERY COMPLETE] Found {len(scanner.sincor_dirs_found)} SINCOR directories")
    scanner.write_sincor_dirs()

    # STEP 2: Scan each SINCOR directory fully
    print("\n=== STEP 2: SCANNING SINCOR DIRECTORIES ===")
    for sincor_dir in scanner.sincor_dirs_found:
        scanner.scan_directory_full(sincor_dir)

    elapsed = time.time() - start_time
    print(f"\n[SCAN COMPLETE] Elapsed: {elapsed:.1f}s")

    # Write outputs
    print("\n=== WRITING OUTPUTS ===")
    scanner.write_manifest_raw()
    scanner.write_manifest_preview()
    scanner.write_top200()
    scanner.print_stats()

    return 0

if __name__ == '__main__':
    sys.exit(main())
