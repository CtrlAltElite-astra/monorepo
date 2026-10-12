// Shared constants. Nothing secret lives here: every app and service imports this file.
import brand from './brand.json' with { type: 'json' };

// Product name, full form and tagline. brand.json is the single source of truth.
export const BRAND = brand;

// Where each piece runs on the demo laptop.
export const URLS = {
  issuerApi: 'http://localhost:4001',
  verifierApi: 'http://localhost:4002',
  wallet: 'http://localhost:5183', // SYNCSHIELD ADAPTATION: 5173 is the SyncShield web app
  issuerWeb: 'http://localhost:5174',
  verifierWeb: 'http://localhost:5175',
};

export const ISSUER_ID = 'issuer:astra-institute';
export const ISSUER_NAME = 'Astra Institute of Technology';
export const CREDENTIAL_TYPE = 'StudentCredential';

// Raw claims are copied from the issuer's student row.
export const RAW_CLAIMS = ['name', 'pnr', 'dob', 'address', 'class', 'section', 'branch', 'photo', 'institution'];
// Derived claims are yes/no facts the issuer computes, so a verifier never needs the raw data behind them.
export const DERIVED_CLAIMS = ['is_student', 'age_over_18'];
export const ALL_CLAIMS = [...RAW_CLAIMS, ...DERIVED_CLAIMS];

// Human-readable names for the consent screen.
export const CLAIM_LABELS: Record<string, string> = {
  name: 'Full name',
  pnr: 'PNR (student number)',
  dob: 'Date of birth',
  address: 'Home address',
  class: 'Class',
  section: 'Section',
  branch: 'Branch',
  photo: 'Photo',
  institution: 'Institution',
  is_student: 'Is a current student',
  age_over_18: 'Is over 18',
};

export * from './syncshield.ts';

// One JSON error shape for every API.
export type ApiError = { error: { code: string; message: string } };
