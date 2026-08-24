/** @type {import('next').NextConfig} */
const nextConfig = {
  // standalone: the Docker runtime stage copies a self-contained server
  // instead of node_modules — much smaller image
  output: "standalone",
};
export default nextConfig;
