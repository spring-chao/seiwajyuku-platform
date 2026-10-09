/** Each section renders as soon as it completes; superseded loads cannot write. */
export function createSectionLoader() {
  let generation = 0;
  return {
    begin() {
      const current = ++generation;
      return async function run<T>(
        request: () => Promise<T>,
        success: (value: T) => void,
        failure: (error: unknown) => void,
        settled: () => void = () => {}
      ) {
        try {
          const value = await request();
          if (current === generation) success(value);
        } catch (error) {
          if (current === generation) failure(error);
        } finally {
          if (current === generation) settled();
        }
      };
    }
  };
}
