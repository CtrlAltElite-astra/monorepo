import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// strictPort: fail loudly instead of silently moving to another port, because the QR links use fixed ports.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5183, strictPort: true },
});
