import React from "react";
import {Composition} from "remotion";
import {TalkCraftVideo, type TalkCraftProps} from "./TalkCraftVideo";

const defaults: TalkCraftProps = {
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 300,
  scenes: [],
  audio: [],
  recipes: {},
  narrationSrc: null,
};

export const TalkCraftRoot: React.FC = () => (
  <Composition<TalkCraftProps>
    id="TalkCraftVideo"
    component={TalkCraftVideo}
    width={defaults.width}
    height={defaults.height}
    fps={defaults.fps}
    durationInFrames={defaults.durationInFrames}
    defaultProps={defaults}
    calculateMetadata={({props}) => ({
      width: props.width,
      height: props.height,
      fps: props.fps,
      durationInFrames: Math.max(1, props.durationInFrames),
    })}
  />
);
