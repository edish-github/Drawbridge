# The operator console and the marketing surfaces.
#
# Next.js standalone output: the build produces a self-contained server with only the modules it
# actually imports, which is what keeps a Node image from being three hundred megabytes of
# devDependencies.

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# `next build` needs to resolve imports, not reach a database. Every page is server-rendered on
# demand, so nothing here queries Firestore at build time.
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=8080

COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

RUN useradd --create-home --shell /usr/sbin/nologin drawbridge \
    && chown -R drawbridge:drawbridge /app
USER drawbridge

EXPOSE 8080
CMD ["node", "server.js"]
