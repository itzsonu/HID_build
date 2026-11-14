export const MAX_ENROLLMENT_PHOTOS = 10;
export const MAX_ENROLLMENT_PHOTO_BYTES = 5 * 1024 * 1024;
export const MAX_ENROLLMENT_IMAGE_EDGE = 1280;

interface PhotoPreparationDependencies {
  decodeImage?: (file: File) => Promise<ImageBitmap>;
  createCanvas?: () => HTMLCanvasElement;
}

function dataUrlBase64(dataUrl: string): string {
  const separator = dataUrl.indexOf(",");
  if (separator < 0 || !dataUrl.startsWith("data:image/jpeg;")) {
    throw new Error("The selected photo could not be converted to JPEG.");
  }
  return dataUrl.slice(separator + 1);
}

export function availableEnrollmentPhotoSlots(photoCount: number): number {
  return Math.max(0, MAX_ENROLLMENT_PHOTOS - photoCount);
}

export async function prepareEnrollmentPhoto(
  file: File,
  dependencies: PhotoPreparationDependencies = {},
): Promise<string> {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    throw new Error("Choose a JPEG, PNG, or WebP image.");
  }
  if (file.size === 0 || file.size > MAX_ENROLLMENT_PHOTO_BYTES) {
    throw new Error("Each reference photo must be smaller than 5 MiB.");
  }

  let bitmap: ImageBitmap;
  try {
    bitmap = await (dependencies.decodeImage ?? createImageBitmap)(file);
  } catch {
    throw new Error("This photo could not be opened. Choose a valid image file.");
  }

  try {
    if (Math.min(bitmap.width, bitmap.height) < 96) {
      throw new Error("The photo is too small; use an image at least 96 pixels wide and tall.");
    }

    const scale = Math.min(
      1,
      MAX_ENROLLMENT_IMAGE_EDGE / Math.max(bitmap.width, bitmap.height),
    );
    const canvas = (dependencies.createCanvas ?? (() => document.createElement("canvas")))();
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    if (!context) {
      throw new Error("Your browser could not prepare this photo.");
    }
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);

    for (const quality of [0.88, 0.78, 0.68]) {
      const jpeg = canvas.toDataURL("image/jpeg", quality);
      if ((jpeg.length - jpeg.indexOf(",") - 1) * 0.75 <= MAX_ENROLLMENT_PHOTO_BYTES) {
        return dataUrlBase64(jpeg);
      }
    }
    throw new Error("This photo is still too large after compression.");
  } finally {
    bitmap.close();
  }
}

function canvasToJpeg(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) {
          resolve(blob);
        } else {
          reject(new Error("The browser could not prepare the captured photo."));
        }
      },
      "image/jpeg",
      quality,
    );
  });
}

export async function captureEnrollmentPhoto(
  video: HTMLVideoElement,
  createCanvas: () => HTMLCanvasElement = () => document.createElement("canvas"),
): Promise<File> {
  const sourceWidth = video.videoWidth;
  const sourceHeight = video.videoHeight;
  if (!sourceWidth || !sourceHeight) {
    throw new Error("The enrollment camera preview is not ready yet.");
  }
  if (Math.min(sourceWidth, sourceHeight) < 96) {
    throw new Error("The camera image is too small; move closer or use another camera.");
  }

  const scale = Math.min(1, MAX_ENROLLMENT_IMAGE_EDGE / Math.max(sourceWidth, sourceHeight));
  const width = Math.max(1, Math.round(sourceWidth * scale));
  const height = Math.max(1, Math.round(sourceHeight * scale));
  const canvas = createCanvas();
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) {
    throw new Error("Your browser could not prepare the captured photo.");
  }
  context.drawImage(video, 0, 0, width, height);

  for (const quality of [0.88, 0.78, 0.68]) {
    const jpeg = await canvasToJpeg(canvas, quality);
    if (jpeg.size > 0 && jpeg.size <= MAX_ENROLLMENT_PHOTO_BYTES) {
      return new File([jpeg], "camera-reference.jpg", { type: "image/jpeg" });
    }
  }
  throw new Error("The captured photo is still too large after compression.");
}

export function stopOwnedCameraStream(
  captureStream: MediaStream | null,
  accessStream: MediaStream | null,
): void {
  if (captureStream && captureStream !== accessStream) {
    captureStream.getTracks().forEach((track) => track.stop());
  }
}
