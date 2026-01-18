const fs = require('fs');
const path = require('path');

const SECRET = '0x1d580c7fc747f69ca4f51a51c06e540a263daa69125ba22e992a0fd2e03bca54';
const REPLACEMENT = '<REDACTED>'; // replacement string

function walk(dir) {
  const items = fs.readdirSync(dir);
  for (const it of items) {
    const p = path.join(dir, it);
    try {
      const st = fs.statSync(p);
      if (st.isDirectory()) {
        // ignore .git directory
        if (it === '.git') continue;
        walk(p);
      } else {
        // Remove sensitive files entirely
        const lower = p.toLowerCase();
        if (lower.endsWith('.env') || lower.endsWith('.env.bak') || it.toLowerCase().includes('atomic') && it.toLowerCase().includes('env')) {
          try { fs.unlinkSync(p); console.log('Removed file', p); } catch (e) {}
          continue;
        }

        // Replace any secret occurrences in files
        let content;
        try { content = fs.readFileSync(p, 'utf8'); } catch (e) { continue; }
        if (content && content.includes(SECRET)) {
          content = content.split(SECRET).join(REPLACEMENT);
          try { fs.writeFileSync(p, content); console.log('Replaced secret in', p); } catch (e) { console.error('Failed write', p, e); }
        }
      }
    } catch (e) {
      // ignore
    }
  }
}

try {
  walk(process.cwd());
  console.log('history-cleaner: done');
} catch (e) {
  console.error('history-cleaner: failed', e);
  process.exit(1);
}
