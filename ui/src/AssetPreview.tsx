import { useState } from "react";
import { assetUrl, type Asset } from "./api";

export const UNAVAILABLE: Record<string, string> = {
  missing: "File not found on this machine",
  not_allowed:
    "Not served: this path is outside the server's --asset-root directories",
};

function Unavailable({ asset, reason }: { asset: Asset; reason: string }) {
  return (
    <div className="asset-missing" role="note">
      <strong>{UNAVAILABLE[reason] ?? "Asset unavailable"}</strong>
      {asset.source_path && <code>{asset.source_path}</code>}
      {reason === "not_allowed" && (
        <span className="muted">
          Start the server on the machine that has the file with{" "}
          <code>hil-eval serve --asset-root DIR</code>.
        </span>
      )}
    </div>
  );
}

export function AssetPreview({ asset }: { asset: Asset }) {
  const [size, setSize] = useState({
    width: asset.width ?? 0,
    height: asset.height ?? 0,
  });
  // Keyed by URL so a failure never leaks to the next item's asset.
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const failed = failedUrl === asset.url;
  if (asset.availability && asset.availability !== "available")
    return <Unavailable asset={asset} reason={asset.availability} />;
  if (failed && asset.kind === "path")
    return <Unavailable asset={asset} reason="missing" />;
  if (asset.mime.startsWith("audio/") || asset.mime.startsWith("video/")) {
    const Media = asset.mime.startsWith("audio/") ? "audio" : "video";
    return (
      <Media
        className="asset"
        controls
        preload="metadata"
        src={assetUrl(asset)}
        onError={() => setFailedUrl(asset.url)}
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
        src={assetUrl(asset)}
        alt={`${asset.role} asset`}
        loading="lazy"
        onError={() => setFailedUrl(asset.url)}
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
