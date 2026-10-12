// Copies the product name, full form and tagline from brand.json into the Markdown headings.
// Markdown cannot import a constant, so this script rewrites the block between the two markers.
// Run with: npm run brand
import { readFileSync, writeFileSync } from 'node:fs';

const brand = JSON.parse(readFileSync('packages/shared/src/brand.json', 'utf8'));
const files = { 'README.md': '', 'PLAN.md': ' — build plan', 'docs/EXPLAIN.md': ' — how each part works, in plain words' };
const START = '<!-- brand:start -->';
const END = '<!-- brand:end -->';

for (const [file, suffix] of Object.entries(files)) {
  const text = readFileSync(file, 'utf8');
  const block = `${START}\n# ${brand.name}${suffix}\n\n**${brand.fullForm}** · *"${brand.tagline}"*\n${END}`;
  const from = text.indexOf(START);
  const to = text.indexOf(END);
  if (from === -1 || to === -1) throw new Error(`${file} is missing the brand markers`);
  writeFileSync(file, text.slice(0, from) + block + text.slice(to + END.length));
  console.log('updated', file);
}
