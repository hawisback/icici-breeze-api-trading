/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  webpack: (config) => {
    // Disable symlink resolution on Windows/OneDrive to prevent EINVAL readlink errors
    config.resolve.symlinks = false;
    return config;
  },
};

export default nextConfig;

