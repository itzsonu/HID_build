export const MIN_FRAME_COUNT = 5;
export const MAX_FRAME_COUNT = 20;
export const MAX_FRAME_BYTES = 5 * 1024 * 1024;
export const MAX_TOTAL_BASE64_BYTES = 20 * 1024 * 1024;

export function decodedBase64Length(value: string): number | null {
  if (!value || value.length % 4 !== 0) {
    return null;
  }

  const padding = value.endsWith("==") ? 2 : value.endsWith("=") ? 1 : 0;
  const decodedLength = (value.length / 4) * 3 - padding;
  if (decodedLength > MAX_FRAME_BYTES) {
    return decodedLength;
  }

  const dataLength = value.length - padding;
  for (let index = 0; index < dataLength; index += 1) {
    const code = value.charCodeAt(index);
    const isLetter = (code >= 65 && code <= 90) || (code >= 97 && code <= 122);
    const isDigit = code >= 48 && code <= 57;
    if (!isLetter && !isDigit && code !== 43 && code !== 47) {
      return null;
    }
  }
  return decodedLength;
}

export function validateFrameBatch(frames: string[]): string | null {
  if (frames.length < MIN_FRAME_COUNT || frames.length > MAX_FRAME_COUNT) {
    return `Capture must contain between ${MIN_FRAME_COUNT} and ${MAX_FRAME_COUNT} frames.`;
  }

  let totalBase64Length = 0;
  for (const [index, frame] of frames.entries()) {
    const decodedLength = decodedBase64Length(frame);
    if (decodedLength === null) {
      return `Frame ${index + 1} is not valid Base64 JPEG data.`;
    }
    if (decodedLength > MAX_FRAME_BYTES) {
      return `Frame ${index + 1} exceeds the 5 MiB image limit.`;
    }
    if (!frame.startsWith("/9j/")) {
      return `Frame ${index + 1} is not a JPEG image.`;
    }

    totalBase64Length += frame.length;
    if (totalBase64Length > MAX_TOTAL_BASE64_BYTES) {
      return "Combined frame data exceeds the 20 MiB request limit.";
    }
  }

  return null;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;

  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize);
    binary += String.fromCharCode(...chunk);
  }

  return btoa(binary);
}

function canvasToJpeg(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) {
          resolve(blob);
        } else {
          reject(new Error("The browser could not encode a camera frame."));
        }
      },
      "image/jpeg",
      quality,
    );
  });
}

export async function captureJpegFrame(video: HTMLVideoElement): Promise<string> {
  const sourceWidth = video.videoWidth;
  const sourceHeight = video.videoHeight;
  if (!sourceWidth || !sourceHeight) {
    throw new Error("The camera preview is not ready yet. Try again in a moment.");
  }

  const canvas = document.createElement("canvas");
  const initialScale = Math.min(1, 640 / Math.max(sourceWidth, sourceHeight));
  let width = Math.max(1, Math.round(sourceWidth * initialScale));
  let height = Math.max(1, Math.round(sourceHeight * initialScale));
  let quality = 0.76;

  for (let attempt = 0; attempt < 4; attempt += 1) {
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) {
      throw new Error("This browser could not prepare a camera frame.");
    }

    context.drawImage(video, 0, 0, width, height);
    const jpeg = await canvasToJpeg(canvas, quality);
    if (jpeg.size <= MAX_FRAME_BYTES) {
      const base64 = bytesToBase64(new Uint8Array(await jpeg.arrayBuffer()));
      const decodedLength = decodedBase64Length(base64);
      if (decodedLength === null || decodedLength > MAX_FRAME_BYTES) {
        throw new Error("The browser could not create a valid frame under 5 MiB.");
      }
      if (!base64.startsWith("/9j/")) {
        throw new Error("The browser did not encode the camera frame as JPEG.");
      }
      return base64;
    }

    width = Math.max(1, Math.floor(width * 0.75));
    height = Math.max(1, Math.floor(height * 0.75));
    quality *= 0.85;
  }

  throw new Error("Could not compress a camera frame below the 5 MiB limit.");
}
