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

  function splitOverlappingTrack(track) {
    const clips = Array.isArray(track && track.clips) ? track.clips : [];
    if (clips.length < 2 || clips.some(clip => !clip || clip.positioned !== true)) return [track];

    const sorted = clips.map((clip, index) => {
      const start = Number(clip.leftPct);
      const width = Number(clip.pct);
      if (!Number.isFinite(start) || !Number.isFinite(width) || start < 0 || width <= 0) return null;
      return {clip, index, start, end: start + width};
    });
    if (sorted.some(item => !item)) return [track];
    sorted.sort((a, b) => a.start - b.start || a.index - b.index);

    const lanes = [];
    for (const item of sorted) {
      // Intervals whose edges touch may share a row; true overlaps never do.
      let lane = lanes.find(candidate => candidate.end <= item.start + 1e-9);
      if (!lane) {
        lane = {end: -Infinity, items: []};
        lanes.push(lane);
      }
      lane.items.push(item);
      lane.end = item.end;
    }
    if (lanes.length <= 1) return [track];

    return lanes.map((lane, index) => ({
      ...track,
      id: String(track.id || "track") + "-lane-" + (index + 1),
      label: index === 0 ? track.label : String(track.label || "轨道") + " " + (index + 1),
      addAction: index === 0 ? track.addAction : null,
      showTrackControls: index === 0,
      clips: lane.items.map(item => item.clip)
    }));
  }

  function visualOverlayStackingZIndex(clip, visualTracks) {
    const tracks = Array.isArray(visualTracks) && visualTracks.length
      ? visualTracks.slice()
      : [{id: "overlay-main", order: 0}];
    tracks.sort((a, b) => {
      const ao = Number.isFinite(Number(a && a.order)) ? Number(a.order) : 0;
      const bo = Number.isFinite(Number(b && b.order)) ? Number(b.order) : 0;
      return ao - bo || String(a && a.id || "").localeCompare(String(b && b.id || ""));
    });
    const trackId = String(clip && clip.track_id || "overlay-main");
    let rank = tracks.findIndex(track => String(track && track.id || "") === trackId);
    if (rank < 0) rank = 0;
    const rawZ = Number(clip && clip.z_index);
    const z = Number.isFinite(rawZ) ? Math.max(-100, Math.min(100, Math.trunc(rawZ))) : 0;
    // Each higher track must render above every clip in lower tracks, even when
    // its local z_index is smaller. +101 keeps all normal overlays above base video.
    return rank * 1000 + z + 101;
  }

  root.TimelineEditMath = Object.freeze({
    trimEndDelta, splitOverlappingTrack, visualOverlayStackingZIndex
  });
})(typeof window !== "undefined" ? window : globalThis);
