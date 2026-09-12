/** Refresh on a fixed cadence, skipping ticks while a request is in flight. */
export function startPolling(
  task: (signal: AbortSignal) => Promise<void>,
  onError: (error: unknown) => void,
  isVisible: () => boolean = () => true,
) {
  const controller = new AbortController();
  let busy = false;
  async function refresh() {
    if (controller.signal.aborted || busy || !isVisible()) return;
    busy = true;
    try {
      await task(controller.signal);
    } catch (error) {
      if (!controller.signal.aborted) onError(error);
    } finally {
      busy = false;
    }
  }
  const timer = setInterval(() => void refresh(), 2000);
  void refresh();
  return {
    refresh: () => void refresh(),
    stop() {
      controller.abort();
      clearInterval(timer);
    },
  };
}
