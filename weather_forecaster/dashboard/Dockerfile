# ─────────────────────────────────────────────────────────────────────────────
# dashboard/Dockerfile
#
# Multi-stage build for the Next.js weather dashboard.
#
# Stages:
#   deps    — install node_modules (cached layer)
#   builder — next build (produces .next/standalone)
#   runner  — minimal production image
#
# The API URL is baked in at build time via NEXT_PUBLIC_API_URL.
# Override it at build time: --build-arg NEXT_PUBLIC_API_URL=http://...
# ─────────────────────────────────────────────────────────────────────────────

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev

# ── Builder ───────────────────────────────────────────────────────────────────
FROM node:22-alpine AS builder
WORKDIR /app

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
RUN npm run build

# ── Runner ────────────────────────────────────────────────────────────────────
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production

# Copy only what next start needs
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/public ./public

EXPOSE 3002

CMD ["npm", "run", "start"]
