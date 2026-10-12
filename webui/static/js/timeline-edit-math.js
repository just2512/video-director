/* Pure timeline edit-boundary math shared by UI and automated regression tests. */
(function installTimelineEditMath(root) {
  "use strict";

  function trimEndDelta(options) {
    const delta = Number(options.delta);
    const start = Number(options.start);
    const duration = Number(options.duration);
    const total = Number(options.total);
    const sourceIn = Number(options.sourceIn);
    const assetDuration = Number(options.assetDuration);
    const minDuration = Number(options.minDuration == null ? 0.05 : options.minDuration);

    if (![delta, start, duration, total, sourceIn, minDuration].every(Number.isFinite)
        || (options.assetType === "video" && !Number.isFinite(assetDuration))) {
      throw new TypeError("trimEndDelta requires finite timeline values");
    }
    if (start < 0 || duration < minDuration || total < start + duration || minDuration <= 0) {
      throw new RangeError("trimEndDelta received an invalid clip interval");
    }

    // The lower bound preserves a non-empty clip. The upper bound prevents a
    // valid pointer gesture from sending an out-of-project range to the server.
    let result = Math.max(delta, -(duration - minDuration));
    result = Math.min(result, Math.max(0, total - start - duration));

    // Video clips also have a source-media boundary; images have no source clock.
    if (options.assetType === "video" && assetDuration > 0) {
      result = Math.min(result, Math.max(0, assetDuration - sourceIn - duration));
    }
    return result;
  }

  root.TimelineEditMath = Object.freeze({trimEndDelta});
})(typeof window !== "undefined" ? window : globalThis);
