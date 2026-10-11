import React from "react";
import {Composition, registerRoot} from "remotion";
import {CreativeVideo, type CreativeVideoProps} from "./CreativeVideo";

const defaults: CreativeVideoProps = {
  width: 1080, height: 1920, fps: 30, durationInFrames: 90,
  shots: [], audioUrl: null, audioStartFrame: 0,
};

const RemotionRoot: React.FC = () => (
  <Composition
    id="CreativeVideo"
    component={CreativeVideo}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={90}
    defaultProps={defaults}
    calculateMetadata={({props}) => ({
      width: Math.max(160, Math.min(3840, Number(props.width) || 1080)),
      height: Math.max(160, Math.min(3840, Number(props.height) || 1920)),
      fps: Math.max(1, Math.min(60, Number(props.fps) || 30)),
      durationInFrames: Math.max(1, Math.min(108000, Number(props.durationInFrames) || 90)),
    })}
  />
);

registerRoot(RemotionRoot);
