/** Each section renders as soon as it completes; superseded loads cannot write. */
export function createSectionLoader() {
  let generation = 0;
  return {
    begin() {
      const current = ++generation;
      return async function run<T>(
        request: () => Promise<T>,
        success: (value: T) => void,
        failure: () => void,
        settled: () => void = () => {}
      ) {
        try {
          const value = await request();
          if (current === generation) success(value);
        } catch {
          if (current === generation) failure();
        } finally {
          if (current === generation) settled();
        }
      };
    }
  };
}
