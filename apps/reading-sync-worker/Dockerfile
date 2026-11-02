FROM mcr.microsoft.com/playwright:v1.62.1-noble
ARG APP_GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$APP_GIT_SHA
ENV APP_GIT_SHA=$APP_GIT_SHA
ENV NODE_ENV=production PORT=8000 TZ=Asia/Shanghai
WORKDIR /worker
COPY apps/reading-sync-worker/package.json apps/reading-sync-worker/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && chown -R pwuser:pwuser /worker
COPY --chown=pwuser:pwuser apps/reading-sync-worker/*.mjs ./
COPY --chown=pwuser:pwuser cloudfunctions/reading-sync-dispatch/periods.mjs ./periods.mjs
USER pwuser
EXPOSE 8000
CMD ["node","server.mjs"]
