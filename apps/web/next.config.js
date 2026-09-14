/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // Parallel dev instances (e.g. live verification on :3100) must not fight
  // over the default .next build dir — give each one its own when isolated.
  distDir: process.env.NEXT_DIST_DIR || '.next',
  async rewrites() {
    const apiBase = process.env.API_INTERNAL_URL || 'http://localhost:8001';
    return [
      {
        source: '/api/:path*',
        destination: `${apiBase}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
