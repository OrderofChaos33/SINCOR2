#!/usr/bin/env python3
"""
Phase 1 - Device-Wide Discovery Scanner
SINCOR Consolidation Mission
Scans device for all SINCOR-related files and generates comprehensive manifest
"""

import os
import sys
import hashlib
import json
import csv
from pathlib import Path
from datetime import datetime
import time

# High-signal keywords (ranked by relevance)
KEYWORDS = [
    'sincor', 'sincor2', 'clinton', 'detailing', 'auto_detail',
    'booking', 'agents', 'observability', 'analytics', 'monetization',
    'revenue', 'flyer', 'canva', 'promo', 'calendar', 'google',
    'compliance', 'jwt', 'limiter', 'security', 'dashboard',
    'archetype', 'agent_analytics', 'agent_interaction', 'almanak'
]

# File extensions of interest
EXTENSIONS_OF_INTEREST = {
    '.py', '.js', '.ts', '.tsx', '.json', '.yml', '.yaml', '.md', '.rst',
    '.ini', '.cfg', '.toml', '.sql', '.ps1', '.bat', '.sh', '.html', '.css',
    '.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp',
    '.pdf', '.docx', '.pptx', '.xlsx', '.csv', '.ipynb',
    '.zip', '.7z', '.rar'
}

# Directories to exclude
EXCLUDE_DIRS = {
    'node_modules', '.git', '.cache', '__pycache__', '.venv', 'venv',
    '.mypy_cache', 'dist', 'build', '.parcel-cache', 'DerivedData',
    'tmp', 'Temp', '.vs', 'bin', 'obj', '$RECYCLE.BIN', 'System Volume Information'
}

class DeviceScanner:
    def __init__(self, roots, staging_dir):
        self.roots = roots
        self.staging_dir = Path(staging_dir)
        self.reports_dir = self.staging_dir / 'reports'
        self.logs_dir = self.staging_dir / 'logs'
        self.manifest = []
        self.stats = {
            'total_scanned': 0,
            'matched': 0,
            'skipped': 0,
            'errors': 0
        }

    def calculate_topic_score(self, path_str):
        """Score file based on keyword matches in path"""
        path_lower = path_str.lower()
        score = 0
        for keyword in KEYWORDS:
            if keyword in path_lower:
                # Higher score for keywords in filename vs parent dirs
                if keyword in Path(path_str).name.lower():
                    score += 10
                else:
                    score += 1
        return score

    def calculate_sha256(self, filepath, max_size=500*1024*1024):
        """Calculate SHA256 hash of file (skip files > 500MB)"""
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
        """Check if directory should be skipped"""
        return dirname in EXCLUDE_DIRS

    def scan_path(self, root):
        """Recursively scan a root path"""
        # Clean up any BOM or special characters
        root = root.strip().strip('\ufeff')
        root_path = Path(root)
        if not root_path.exists():
            print(f"[SKIP] Root not found: {root_path}")
            return

        print(f"\n[SCANNING] {root_path}")

        for dirpath, dirnames, filenames in os.walk(root):
            # Filter out excluded directories (modifies in-place)
            dirnames[:] = [d for d in dirnames if not self.should_skip_dir(d)]

            for filename in filenames:
                self.stats['total_scanned'] += 1

                if self.stats['total_scanned'] % 1000 == 0:
                    print(f"  Scanned {self.stats['total_scanned']} files, matched {self.stats['matched']}...")

                filepath = Path(dirpath) / filename

                try:
                    # Check extension
                    ext = filepath.suffix.lower()
                    if ext not in EXTENSIONS_OF_INTEREST:
                        self.stats['skipped'] += 1
                        continue

                    # Calculate topic score
                    topic_score = self.calculate_topic_score(str(filepath))

                    # If no keyword match and not in SINCOR paths, skip
                    path_str = str(filepath).lower()
                    if topic_score == 0 and 'sincor' not in path_str:
                        self.stats['skipped'] += 1
                        continue

                    # Get file stats
                    stat = filepath.stat()
                    mtime = datetime.fromtimestamp(stat.st_mtime)

                    # Calculate hash (for matched files only)
                    sha256 = self.calculate_sha256(str(filepath))

                    # Create manifest entry
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
                    print(f"  [ERROR] {filepath}: {e}")

    def write_manifest_raw(self):
        """Write raw JSONL manifest"""
        output_file = self.reports_dir / 'manifest_raw.jsonl'
        with open(output_file, 'w', encoding='utf-8') as f:
            for entry in self.manifest:
                f.write(json.dumps(entry) + '\n')
        print(f"\n[WRITTEN] {output_file} ({len(self.manifest)} entries)")

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
        """Write top 200 files by topic score and recency"""
        # Sort by topic_score desc, then mtime desc
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
        """Print summary statistics"""
        print(f"\n{'='*60}")
        print("PHASE 1 COMPLETE - Device-Wide Discovery")
        print(f"{'='*60}")
        print(f"Total files scanned: {self.stats['total_scanned']:,}")
        print(f"Matched files: {self.stats['matched']:,}")
        print(f"Skipped files: {self.stats['skipped']:,}")
        print(f"Errors: {self.stats['errors']:,}")
        print(f"\nManifest entries: {len(self.manifest):,}")
        print(f"Total size: {sum(e['size'] for e in self.manifest) / (1024**3):.2f} GB")
        print(f"{'='*60}")

def main():
    # Define paths
    staging_dir = Path(r'C:\_sincor_consolidation')

    # Read roots from file
    roots_file = staging_dir / 'reports' / 'roots.txt'
    if not roots_file.exists():
        print(f"[ERROR] roots.txt not found at {roots_file}")
        sys.exit(1)

    with open(roots_file, 'r', encoding='utf-8') as f:
        roots = [line.strip() for line in f if line.strip()]

    print("="*60)
    print("PHASE 1 - Device-Wide Discovery Scanner")
    print("="*60)
    print(f"Staging directory: {staging_dir}")
    print(f"Search roots: {len(roots)}")
    print(f"Keywords tracked: {len(KEYWORDS)}")
    print(f"Extensions tracked: {len(EXTENSIONS_OF_INTEREST)}")
    print("="*60)

    # Initialize scanner
    scanner = DeviceScanner(roots, staging_dir)

    # Scan all roots
    start_time = time.time()
    for root in roots:
        scanner.scan_path(root)

    elapsed = time.time() - start_time
    print(f"\n[SCAN COMPLETE] Elapsed time: {elapsed:.1f}s")

    # Write outputs
    print("\n[WRITING OUTPUTS]")
    scanner.write_manifest_raw()
    scanner.write_manifest_preview()
    scanner.write_top200()

    # Print stats
    scanner.print_stats()

    return 0

if __name__ == '__main__':
    sys.exit(main())
