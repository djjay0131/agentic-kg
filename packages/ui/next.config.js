/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  env: {
    // Use NEXT_PUBLIC_API_URL from build args, fallback to runtime API_URL for local dev
    API_URL: process.env.NEXT_PUBLIC_API_URL || process.env.API_URL || 'http://localhost:8000',
  },
}

module.exports = nextConfig
