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

export interface FilmTimelineTrimResolution {
  target: number;
  deltaSeconds: number;
  snapCandidate: FilmTimelineSnapCandidate | null;
}

export interface FilmTimelineLinkedEditResolution {
  targetAt: number;
  deltaSeconds: number;
  snapCandidate: FilmTimelineSnapCandidate | null;
}
export interface FilmTimelineSlipResolution {
  sourceDeltaSeconds: number;
}

export interface FilmTimelineEnvelopePoint {
  t: number;
  v: number;
}
export interface FilmTimelineEnvelopePointerDownResolution {
  kind: "none" | "minimum-points" | "remove" | "edit" | "insert";
  index: number;
  points: FilmTimelineEnvelopePoint[];
  added: boolean;
}

export interface FilmTimelineMathBridge {
  secondsToPixels(seconds: number, pixelsPerSecond: number): number;
  pixelsToSeconds(pixels: number, pixelsPerSecond: number): number;
  dragDeltaSeconds(pointerDeltaPixels: number, scrollDeltaPixels: number, pixelsPerSecond: number): number;
  resolveSnapCandidate(rawAt: number, candidates: readonly FilmTimelineSnapCandidate[], pixelsPerSecond: number, thresholdPixels: number, frameRate?: number): FilmTimelineSnapCandidate | null;
  resolveMoveGesture(minimumStart: number, rawDeltaSeconds: number, candidates: readonly FilmTimelineSnapCandidate[], pixelsPerSecond: number, thresholdPixels: number, snapEnabled: boolean, frameRate?: number): FilmTimelineMoveResolution;
  resolveTrimStart(originalAt: number, originalEnd: number, rawDeltaSeconds: number, candidates: readonly FilmTimelineSnapCandidate[], pixelsPerSecond: number, thresholdPixels: number, snapEnabled: boolean, frameRate?: number): FilmTimelineTrimResolution;
  resolveTrimEnd(originalAt: number, originalEnd: number, rawDeltaSeconds: number, candidates: readonly FilmTimelineSnapCandidate[], pixelsPerSecond: number, thresholdPixels: number, snapEnabled: boolean, frameRate?: number): FilmTimelineTrimResolution;
  resolveRollGesture(rightAt: number, rawDeltaSeconds: number, leftDuration: number, rightDuration: number, candidates: readonly FilmTimelineSnapCandidate[], pixelsPerSecond: number, thresholdPixels: number, snapEnabled: boolean, frameRate?: number): FilmTimelineLinkedEditResolution;
  resolveSlideGesture(centerAt: number, rawDeltaSeconds: number, leftDuration: number, centerDuration: number, rightDuration: number, candidates: readonly FilmTimelineSnapCandidate[], pixelsPerSecond: number, thresholdPixels: number, snapEnabled: boolean, frameRate?: number): FilmTimelineLinkedEditResolution;
  resolveSlipGesture(rawDeltaSeconds: number, playbackSpeed: number, snapEnabled?: boolean, frameRate?: number): FilmTimelineSlipResolution;
  resolveEnvelopePointerDown(
    points: readonly FilmTimelineEnvelopePoint[], duration: number, width: number, height: number,
    localX: number, localY: number, button: number, shiftKey: boolean,
  ): FilmTimelineEnvelopePointerDownResolution;
  moveEnvelopeKeyframe(
    points: readonly FilmTimelineEnvelopePoint[], index: number, duration: number, width: number, height: number,
    localX: number, localY: number,
  ): FilmTimelineEnvelopePoint[] | null;
  normalizeEnvelopePoints(points: readonly FilmTimelineEnvelopePoint[]): FilmTimelineEnvelopePoint[];
  clipLeft(at: number, pixelsPerSecond: number): number;
  clipWidth(duration: number | null, pixelsPerSecond: number, minWidth?: number, unknownDuration?: number, maxWidth?: number, roundUp?: boolean): number;
  zoomScale(current: number, direction: number, min?: number, max?: number): number;
  zoomAnchorTime(scrollLeft: number, localX: number, pixelsPerSecond: number, headerWidth: number): number;
  zoomScrollLeft(anchorTime: number, localX: number, pixelsPerSecond: number, headerWidth: number): number;
  snapToFrame(seconds: number, fps: number): number;
}

const finiteOr = (value: number | undefined, fallback: number): number =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

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
  resolveSnapCandidate(rawAt, candidates, pixelsPerSecond, thresholdPixels, frameRate) {
    const target = finiteOr(rawAt, 0);
    const threshold = Math.max(0, finiteOr(thresholdPixels, 0));
    let best: FilmTimelineSnapCandidate | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const candidate of candidates) {
      if (!candidate || !Number.isFinite(candidate.at)) continue;
      const distance = Math.abs(filmTimelineMath.secondsToPixels(candidate.at - target, pixelsPerSecond));
      // Explicit head/playhead/clip candidates retain priority and stable tie order.
      if (distance <= threshold && distance < bestDistance) {
        best = candidate;
        bestDistance = distance;
      }
    }
    if (best) return best;
    const fps = finiteOr(frameRate, 0);
    if (fps > 0) {
      const frameAt = Math.max(0, filmTimelineMath.snapToFrame(Math.max(0, target), fps));
      const frameDistance = Math.abs(filmTimelineMath.secondsToPixels(frameAt - target, pixelsPerSecond));
      if (frameDistance <= threshold) return {at:frameAt, kind:"帧"};
    }
    return null;
  },
  resolveMoveGesture(minimumStart, rawDeltaSeconds, candidates, pixelsPerSecond, thresholdPixels, snapEnabled, frameRate) {
    const start = Math.max(0, finiteOr(minimumStart, 0));
    // Resolve against the same zero-clamped play time in both preview and commit.
    const rawAt = Math.max(0, start + finiteOr(rawDeltaSeconds, 0));
    const candidate = snapEnabled
      ? filmTimelineMath.resolveSnapCandidate(rawAt, candidates, pixelsPerSecond, thresholdPixels, frameRate)
      : null;
    const proposedAt = candidate ? Math.max(0, candidate.at) : rawAt;
    const deltaSeconds = Math.max(-start, proposedAt - start);
    const targetAt = Math.max(0, start + deltaSeconds);
    const snapCandidate = candidate && Math.abs(targetAt - candidate.at) < 0.001
      ? candidate
      : null;
    return {targetAt, deltaSeconds, snapCandidate};
  },
  resolveTrimStart(originalAt, originalEnd, rawDeltaSeconds, candidates, pixelsPerSecond, thresholdPixels, snapEnabled, frameRate) {
    const start = finiteOr(originalAt, 0);
    const end = finiteOr(originalEnd, start + 0.08);
    const rawTarget = start + finiteOr(rawDeltaSeconds, 0);
    const candidate = snapEnabled
      ? filmTimelineMath.resolveSnapCandidate(rawTarget, candidates, pixelsPerSecond, thresholdPixels, frameRate)
      : null;
    const proposed = candidate ? candidate.at : rawTarget;
    const target = Math.max(start + 0.04, Math.min(end - 0.04, proposed));
    const snapCandidate = candidate && Math.abs(target - candidate.at) < 0.001 ? candidate : null;
    return {target, deltaSeconds: target - start, snapCandidate};
  },
  resolveTrimEnd(originalAt, originalEnd, rawDeltaSeconds, candidates, pixelsPerSecond, thresholdPixels, snapEnabled, frameRate) {
    const start = finiteOr(originalAt, 0);
    const end = finiteOr(originalEnd, start + 0.08);
    const rawTarget = end + finiteOr(rawDeltaSeconds, 0);
    const candidate = snapEnabled
      ? filmTimelineMath.resolveSnapCandidate(rawTarget, candidates, pixelsPerSecond, thresholdPixels, frameRate)
      : null;
    const proposed = candidate ? candidate.at : rawTarget;
    const target = Math.max(start + 0.04, Math.min(end - 0.04, proposed));
    const snapCandidate = candidate && Math.abs(target - candidate.at) < 0.001 ? candidate : null;
    return {target, deltaSeconds: target - end, snapCandidate};
  },
  resolveRollGesture(rightAt, rawDeltaSeconds, leftDuration, rightDuration, candidates, pixelsPerSecond, thresholdPixels, snapEnabled, frameRate) {
    const anchor = finiteOr(rightAt, 0);
    const rawDelta = finiteOr(rawDeltaSeconds, 0);
    const candidate = snapEnabled
      ? filmTimelineMath.resolveSnapCandidate(anchor + rawDelta, candidates, pixelsPerSecond, thresholdPixels, frameRate)
      : null;
    const proposedDelta = candidate ? candidate.at - anchor : rawDelta;
    const deltaSeconds = Math.max(
      -Math.max(0, finiteOr(leftDuration, 0)) + 0.04,
      Math.min(Math.max(0, finiteOr(rightDuration, 0)) - 0.04, proposedDelta),
    );
    const targetAt = anchor + deltaSeconds;
    const snapCandidate = candidate && Math.abs(targetAt - candidate.at) < 0.001 ? candidate : null;
    return {targetAt, deltaSeconds, snapCandidate};
  },
  resolveSlideGesture(centerAt, rawDeltaSeconds, leftDuration, centerDuration, rightDuration, candidates, pixelsPerSecond, thresholdPixels, snapEnabled, frameRate) {
    const anchor = finiteOr(centerAt, 0);
    const rawDelta = finiteOr(rawDeltaSeconds, 0);
    const candidate = snapEnabled
      ? filmTimelineMath.resolveSnapCandidate(anchor + rawDelta, candidates, pixelsPerSecond, thresholdPixels, frameRate)
      : null;
    const proposedDelta = candidate ? candidate.at - anchor : rawDelta;
    const minimumDelta = -Math.min(
      Math.max(0, finiteOr(leftDuration, 0)),
      Math.max(0, finiteOr(centerDuration, 0)),
    ) + 0.04;
    const maximumDelta = Math.min(
      Math.max(0, finiteOr(centerDuration, 0)),
      Math.max(0, finiteOr(rightDuration, 0)),
    ) - 0.04;
    const deltaSeconds = Math.max(minimumDelta, Math.min(maximumDelta, proposedDelta));
    const targetAt = anchor + deltaSeconds;
    const snapCandidate = candidate && Math.abs(targetAt - candidate.at) < 0.001 ? candidate : null;
    return {targetAt, deltaSeconds, snapCandidate};
  },
  resolveSlipGesture(rawDeltaSeconds, playbackSpeed, snapEnabled, frameRate) {
    // Slip aligns the source offset to frames when snapping is enabled. The sign
    // remains meaningful: negative offsets move the source window earlier.
    let sourceDeltaSeconds = finiteOr(rawDeltaSeconds, 0) * finiteOr(playbackSpeed, 1);
    const fps = finiteOr(frameRate, 0);
    if (snapEnabled && fps > 0) sourceDeltaSeconds = filmTimelineMath.snapToFrame(sourceDeltaSeconds, fps);
    return {sourceDeltaSeconds};
  },
  resolveEnvelopePointerDown(points, duration, width, height, localX, localY, button, shiftKey) {
    const copied = points.map(point => ({t:point.t, v:point.v}));
    const none = (): FilmTimelineEnvelopePointerDownResolution => ({
      kind:"none", index:-1, points:copied.map(point => ({...point})), added:false,
    });
    const safeDuration = finiteOr(duration, 0);
    const safeWidth = finiteOr(width, 0);
    const safeHeight = finiteOr(height, 0);
    if (safeDuration <= 0.02 || safeWidth <= 0 || safeHeight <= 0) return none();
    const x = finiteOr(localX, 0);
    const y = finiteOr(localY, 0);

    // Shift/right-click deletes the closest interior keyframe by horizontal distance,
    // preserving the editor's 12 px hit zone and endpoint protection.
    if (button === 2 || shiftKey) {
      let nearest = -1;
      let nearestPixels = Number.POSITIVE_INFINITY;
      copied.forEach((point, index) => {
        if (point.t <= 0.0001 || point.t >= safeDuration - 0.0001) return;
        const px = Math.abs(point.t / safeDuration * safeWidth - x);
        if (px < nearestPixels) {
          nearestPixels = px;
          nearest = index;
        }
      });
      if (nearest >= 0 && nearestPixels <= 12) {
        const next = copied.map(point => ({...point}));
        next.splice(nearest, 1);
        if (next.length < 2) {
          return {kind:"minimum-points", index:nearest, points:copied.map(point => ({...point})), added:false};
        }
        return {kind:"remove", index:-1, points:next, added:false};
      }
      // Shift+left-click with no delete target retains ordinary select/insert behaviour.
      if (button !== 0) return none();
    }
    if (button !== 0) return none();

    // Match the legacy click tolerance. Iteration order is retained for stacked points.
    let index = -1;
    copied.forEach((point, candidateIndex) => {
      const px = Math.abs(point.t / safeDuration * safeWidth - x);
      const renderedY = safeHeight - 4 - point.v * (safeHeight - 8);
      const py = Math.abs(renderedY - y);
      if (px <= 10 && py <= 10) index = candidateIndex;
    });
    if (index >= 0) {
      return {kind:"edit", index, points:copied, added:false};
    }

    const time = Math.max(0.01, Math.min(
      safeDuration - 0.01,
      Math.max(0, Math.min(safeDuration, x / safeWidth * safeDuration)),
    ));
    const value = Math.max(0, Math.min(1, 1 - (y - 4) / Math.max(1, safeHeight - 8)));
    index = copied.findIndex(point => point.t > time);
    if (index < 0) index = copied.length;
    copied.splice(index, 0, {t:time, v:value});
    return {kind:"insert", index, points:copied, added:true};
  },
  moveEnvelopeKeyframe(points, index, duration, width, height, localX, localY) {
    const safeDuration = finiteOr(duration, 0);
    const safeWidth = finiteOr(width, 0);
    const safeHeight = finiteOr(height, 0);
    if (safeDuration <= 0.02 || safeWidth <= 0 || safeHeight <= 0 ||
        !Number.isInteger(index) || index < 0 || index >= points.length) return null;
    const next = points.map(point => ({t:point.t, v:point.v}));
    const point = next[index];
    const x = Math.max(0, Math.min(safeWidth, finiteOr(localX, 0)));
    const y = Math.max(0, Math.min(safeHeight, finiteOr(localY, 0)));
    if (index > 0 && index < next.length - 1) {
      const lower = next[index - 1].t + 0.01;
      const upper = next[index + 1].t - 0.01;
      point.t = Math.max(lower, Math.min(upper, x / safeWidth * safeDuration));
    }
    point.v = Math.max(0, Math.min(1, 1 - (y - 4) / Math.max(1, safeHeight - 8)));
    return next;
  },
  normalizeEnvelopePoints(points) {
    const normalized = points.map(point => ({
      t:Number(point.t.toFixed(3)),
      v:Number(point.v.toFixed(3)),
    })).sort((a,b) => a.t - b.t);
    const unique: FilmTimelineEnvelopePoint[] = [];
    normalized.forEach(point => {
      if (unique.length && Math.abs(unique[unique.length - 1].t - point.t) < 0.001) {
        unique[unique.length - 1] = point;
      } else {
        unique.push(point);
      }
    });
    return unique;
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
