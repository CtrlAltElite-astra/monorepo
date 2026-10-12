// Starts everything with one command: npm run dev
// Two APIs and three web apps, each as a child process with a coloured name prefix.
import { spawn, spawnSync } from 'node:child_process';

const processes = [
  ['issuer-api  ', '@true/issuer-api'],
  ['verifier-api', '@true/verifier-api'],
  ['wallet      ', '@true/wallet'],
  ['issuer-web  ', '@true/issuer-web'],
  ['verifier-web', '@true/verifier-web'],
];

const children = processes.map(([label, workspace], i) => {
  // shell: true is needed on Windows, where npm is a .cmd file.
  const child = spawn(`npm run dev -w ${workspace}`, { shell: true });
  const prefix = `\x1b[3${i + 2}m[${label}]\x1b[0m `;
  const print = (chunk) => {
    for (const line of chunk.toString().split('\n')) if (line.trim()) console.log(prefix + line);
  };
  child.stdout.on('data', print);
  child.stderr.on('data', print);
  return child;
});

// Ctrl+C stops all of them.
// On Windows each child is a shell that started npm that started node, so we must stop the whole tree,
// otherwise the ports stay busy.
process.on('SIGINT', () => {
  for (const child of children) {
    if (process.platform === 'win32') spawnSync(`taskkill /pid ${child.pid} /T /F`, { shell: true });
    else child.kill();
  }
  process.exit(0);
});

console.log('\nWallet        http://localhost:5173\nIssuer admin  http://localhost:5174\nVerifier      http://localhost:5175\n');
