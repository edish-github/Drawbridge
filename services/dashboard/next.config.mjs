/**
 * The dashboard is local-only and reads the Firestore emulator through the Node client, which
 * pulls in gRPC. Marking it external keeps the bundler from trying to trace a native module it
 * cannot bundle, which is the failure this config exists to prevent.
 */
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

/** @type {import('next').NextConfig} */
const nextConfig = {
  serverExternalPackages: ["@google-cloud/firestore"],
  // Pinned, because the dashboard lives inside a Python repository and the inference walks up
  // until it finds a lockfile — which on a developer machine can be one in a home directory.
  outputFileTracingRoot: dirname(fileURLToPath(import.meta.url)),
};

export default nextConfig;
