// Issuer database: the source of truth for who may receive a credential.
// Uses node:sqlite, which is built into Node 24 (nothing to install).
import { DatabaseSync } from 'node:sqlite';
import { migrateSyncShield } from './syncshield.ts';

export type Student = {
  id: number;
  name: string;
  pnr: string;
  dob: string; // YYYY-MM-DD
  address: string;
  class: string;
  section: string;
  branch: string;
  photo: string; // small SVG data URI, stored here and in the wallet only
  status: 'active' | 'revoked';
  revoked_reason: string | null;
  status_list_index: number | null;
  created_at: string;
};

export type ClaimCode = {
  code: string;
  student_id: number;
  nonce: string;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  profile?: string; // SYNCSHIELD ADAPTATION: 'student' (original) or 'syncshield'
};

const SCHEMA = `
CREATE TABLE IF NOT EXISTS students (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  pnr TEXT NOT NULL UNIQUE,
  dob TEXT NOT NULL,
  address TEXT NOT NULL,
  class TEXT NOT NULL,
  section TEXT NOT NULL,
  branch TEXT NOT NULL,
  photo TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  revoked_reason TEXT,
  status_list_index INTEGER UNIQUE,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_codes (
  code TEXT PRIMARY KEY,
  student_id INTEGER NOT NULL REFERENCES students(id),
  nonce TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);
`;

// All demo students are invented. No real person's data is used.
const FAKE_STUDENTS = [
  ['Aarav Deshmukh', 'AIT2023001', '2005-03-14', '12 Lake View Road, Pune', 'TE', 'A', 'Computer Engineering'],
  ['Isha Kulkarni', 'AIT2023002', '2004-11-02', '7 Hill Crest Lane, Pune', 'TE', 'A', 'Computer Engineering'],
  ['Rohan Patil', 'AIT2024003', '2009-06-21', '45 Station Road, Nashik', 'FE', 'B', 'Information Technology'],
  ['Meera Joshi', 'AIT2022004', '2003-01-30', '3 Garden Street, Mumbai', 'BE', 'C', 'Electronics'],
  ['Kabir Shaikh', 'AIT2023005', '2005-09-09', '88 Market Yard, Solapur', 'TE', 'B', 'Mechanical'],
  ['Ananya Rao', 'AIT2024006', '2006-12-25', '19 Temple Road, Kolhapur', 'SE', 'A', 'Computer Engineering'],
  ['Dev Mehta', 'AIT2022007', '2003-07-07', '60 Riverside Colony, Nagpur', 'BE', 'A', 'Civil'],
  ['Sara Fernandes', 'AIT2024008', '2009-02-18', '5 Church Street, Panaji', 'FE', 'C', 'Information Technology'],
];

// Placeholder photo: the student's initials on a coloured square. Drawn by us, so no third-party image.
function placeholderPhoto(name: string, index: number): string {
  const initials = name.split(' ').map((part) => part[0]).join('');
  const hue = (index * 47) % 360;
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96">` +
    `<rect width="96" height="96" rx="12" fill="hsl(${hue} 55% 45%)"/>` +
    `<text x="48" y="60" font-size="36" font-family="sans-serif" fill="white" text-anchor="middle">${initials}</text></svg>`;
  return 'data:image/svg+xml,' + encodeURIComponent(svg);
}

// Opens (and on first run creates and seeds) the issuer database. Use ':memory:' in tests.
export function openIssuerDb(path: string): DatabaseSync {
  const db = new DatabaseSync(path);
  db.exec(SCHEMA);
  migrateSyncShield(db); // SYNCSHIELD ADAPTATION: additive tables and claim_codes.profile
  const count = db.prepare('SELECT COUNT(*) AS n FROM students').get() as { n: number };
  if (count.n === 0) {
    const insert = db.prepare(
      `INSERT INTO students (name, pnr, dob, address, class, section, branch, photo, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    );
    FAKE_STUDENTS.forEach((s, i) => insert.run(...s, placeholderPhoto(s[0], i), new Date().toISOString()));
  }
  return db;
}
