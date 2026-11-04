import { useState } from "react";
import { imageUrl, type Asset } from "./api";

export function AssetPreview({ asset }: { asset: Asset }) {
  const [size, setSize] = useState({
    width: asset.width ?? 0,
    height: asset.height ?? 0,
  });
  if (asset.mime.startsWith("audio/") || asset.mime.startsWith("video/")) {
    const Media = asset.mime.startsWith("audio/") ? "audio" : "video";
    return (
      <Media
        className="asset"
        controls
        preload="metadata"
        src={imageUrl(asset.sha256)}
        onLoadedMetadata={(e) => {
          if (asset.video_ts !== null)
            e.currentTarget.currentTime = Math.min(
              asset.video_ts,
              e.currentTarget.duration || asset.video_ts,
            );
        }}
      />
    );
  }
  return (
    <div className="image-frame">
      <img
        className="asset"
        src={imageUrl(asset.sha256)}
        alt={`${asset.role} asset`}
        loading="lazy"
        onLoad={(e) =>
          setSize({
            width: e.currentTarget.naturalWidth,
            height: e.currentTarget.naturalHeight,
          })
        }
      />
      {asset.bbox && size.width > 0 && size.height > 0 && (
        <span
          aria-label="Bounding box"
          className="bbox"
          style={{
            left: `${(asset.bbox[0] / size.width) * 100}%`,
            top: `${(asset.bbox[1] / size.height) * 100}%`,
            width: `${(asset.bbox[2] / size.width) * 100}%`,
            height: `${(asset.bbox[3] / size.height) * 100}%`,
          }}
        />
      )}
    </div>
  );
}
