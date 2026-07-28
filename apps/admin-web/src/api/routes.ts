type Result = {
  success: boolean;
  data: Array<any>;
};

/**
 * This deployment uses the statically bundled route modules under
 * src/router/modules. The API does not expose /get-async-routes, so making
 * that request leaves the sidebar in its loading state forever after refresh.
 */
export const getAsyncRoutes = async (): Promise<Result> => ({
  success: true,
  data: []
});
