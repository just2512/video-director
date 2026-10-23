/** Shared timeline and gesture math used by React surfaces and the Studio operation adapter. */
export interface FilmTimelineSnapCandidate {
  at: number;
  kind: string;
}

export interface FilmTimelineMoveResolution {
  targetAt: number;
  deltaSeconds: number;
  snapCandidate: FilmTimelineSnapCandidate | null;
}

export interface FilmTimelineMathBridge {
  secondsToPixels(seconds: number, pixelsPerSecond: number): number;
  pixelsToSeconds(pixels: number, pixelsPerSecond: number): number;
  dragDeltaSeconds(pointerDeltaPixels: number, scrollDeltaPixels: number, pixelsPerSecond: number): number;
  resolveSnapCandidate(rawAt: number, candidates: readonly FilmTimelineSnapCandidate[], pixelsPerSecond: number, thresholdPixels: number): FilmTimelineSnapCandidate | null;
  resolveMoveGesture(minimumStart: number, rawDeltaSeconds: number, candidates: readonly FilmTimelineSnapCandidate[], pixelsPerSecond: number, thresholdPixels: number, snapEnabled: boolean): FilmTimelineMoveResolution;
  clipLeft(at: number, pixelsPerSecond: number): number;
  clipWidth(duration: number | null, pixelsPerSecond: number, minWidth?: number, unknownDuration?: number, maxWidth?: number, roundUp?: boolean): number;
  zoomScale(current: number, direction: number, min?: number, max?: number): number;
  zoomAnchorTime(scrollLeft: number, localX: number, pixelsPerSecond: number, headerWidth: number): number;
  zoomScrollLeft(anchorTime: number, localX: number, pixelsPerSecond: number, headerWidth: number): number;
  snapToFrame(seconds: number, fps: number): number;
}

const finiteOr = (value: number, fallback: number): number =>
  Number.isFinite(value) ? value : fallback;

export const filmTimelineMath: FilmTimelineMathBridge = {
  secondsToPixels(seconds, pixelsPerSecond) {
    return finiteOr(seconds, 0) * Math.max(0.000001, finiteOr(pixelsPerSecond, 1));
  },
  pixelsToSeconds(pixels, pixelsPerSecond) {
    return finiteOr(pixels, 0) / Math.max(0.000001, finiteOr(pixelsPerSecond, 1));
  },
  dragDeltaSeconds(pointerDeltaPixels, scrollDeltaPixels, pixelsPerSecond) {
    return filmTimelineMath.pixelsToSeconds(
      finiteOr(pointerDeltaPixels, 0) + finiteOr(scrollDeltaPixels, 0),
      pixelsPerSecond,
    );
  },
  resolveSnapCandidate(rawAt, candidates, pixelsPerSecond, thresholdPixels) {
    const target = finiteOr(rawAt, 0);
    const threshold = Math.max(0, finiteOr(thresholdPixels, 0));
    let best: FilmTimelineSnapCandidate | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const candidate of candidates) {
      if (!candidate || !Number.isFinite(candidate.at)) continue;
      const distance = Math.abs(filmTimelineMath.secondsToPixels(candidate.at - target, pixelsPerSecond));
      // Strictly-less preserves candidate order when distances tie.
      if (distance <= threshold && distance < bestDistance) {
        best = candidate;
        bestDistance = distance;
      }
    }
    return best;
  },
  resolveMoveGesture(minimumStart, rawDeltaSeconds, candidates, pixelsPerSecond, thresholdPixels, snapEnabled) {
    const start = Math.max(0, finiteOr(minimumStart, 0));
    // Resolve against the same zero-clamped play time in both preview and commit.
    const rawAt = Math.max(0, start + finiteOr(rawDeltaSeconds, 0));
    const candidate = snapEnabled
      ? filmTimelineMath.resolveSnapCandidate(rawAt, candidates, pixelsPerSecond, thresholdPixels)
      : null;
    const proposedAt = candidate ? Math.max(0, candidate.at) : rawAt;
    const deltaSeconds = Math.max(-start, proposedAt - start);
    const targetAt = Math.max(0, start + deltaSeconds);
    const snapCandidate = candidate && Math.abs(targetAt - candidate.at) < 0.001
      ? candidate
      : null;
    return {targetAt, deltaSeconds, snapCandidate};
  },
  clipLeft(at, pixelsPerSecond) {
    return Math.max(0, filmTimelineMath.secondsToPixels(at, pixelsPerSecond));
  },
  clipWidth(duration, pixelsPerSecond, minWidth = 24, unknownDuration = 2, maxWidth = 0, roundUp = false) {
    const seconds = duration == null ? unknownDuration : Math.max(0, finiteOr(duration, 0));
    let width = filmTimelineMath.secondsToPixels(seconds, pixelsPerSecond);
    if (roundUp) width = Math.ceil(width);
    const ceiling = maxWidth > 0 ? maxWidth : Number.POSITIVE_INFINITY;
    return Math.max(minWidth, Math.min(ceiling, width));
  },
  zoomScale(current, direction, min = 12, max = 120) {
    const next = finiteOr(current, min) * (direction > 0 ? 1.25 : 0.8);
    return Math.max(min, Math.min(max, next));
  },
  zoomAnchorTime(scrollLeft, localX, pixelsPerSecond, headerWidth) {
    return Math.max(0, filmTimelineMath.pixelsToSeconds(
      finiteOr(scrollLeft, 0) + finiteOr(localX, 0) - finiteOr(headerWidth, 0),
      pixelsPerSecond,
    ));
  },
  zoomScrollLeft(anchorTime, localX, pixelsPerSecond, headerWidth) {
    return Math.max(0,
      finiteOr(headerWidth, 0) +
      filmTimelineMath.secondsToPixels(anchorTime, pixelsPerSecond) -
      finiteOr(localX, 0),
    );
  },
  snapToFrame(seconds, fps) {
    const safeFps = Math.max(1, Math.min(120, finiteOr(fps, 30)));
    return Math.round(finiteOr(seconds, 0) * safeFps) / safeFps;
  },
};
