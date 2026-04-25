// PhotoSwipe wrapper — `openCoverLightbox(url)` measures the image's
// natural dimensions then opens a single-image lightbox with zoom,
// pan, swipe-to-close, and keyboard navigation. Saves us from
// hand-rolling another modal + zoom layer.
//
// PhotoSwipe is dynamically imported on first use so its ~80KB only
// hits the network when the user actually clicks a cover. Saves the
// initial-bundle cost for users who never enlarge anything.
//
// PhotoSwipe v5 wants intrinsic image dimensions up-front (`width`
// + `height` on the data source) so it can compute the placeholder
// box before the bitmap loads. Cover endpoints don't expose metadata,
// so we briefly preload via `new Image()` to read naturalWidth/
// naturalHeight, then init PhotoSwipe with the actual dimensions.

export async function openCoverLightbox(url: string): Promise<void> {
  if (!url) return;

  // Kick off image preload + module load in parallel — both take
  // the same wall-clock to first-paint of the lightbox.
  const [dims, modules] = await Promise.all([
    new Promise<{ w: number; h: number } | null>((resolve) => {
      const img = new Image();
      img.onload = () => {
        resolve({ w: img.naturalWidth, h: img.naturalHeight });
      };
      img.onerror = () => resolve(null);
      img.src = url;
    }),
    Promise.all([
      import("photoswipe/lightbox"),
      import("photoswipe"),
      import("photoswipe/style.css"),
    ]),
  ]);

  if (!dims) return;
  const PhotoSwipeLightbox = modules[0].default;
  const PhotoSwipe = modules[1].default;

  const lightbox = new PhotoSwipeLightbox({
    pswpModule: PhotoSwipe,
    dataSource: [
      {
        src: url,
        width: dims.w,
        height: dims.h,
        alt: "Cover",
      },
    ],
    showHideAnimationType: "fade",
    bgOpacity: 0.92,
    closeOnVerticalDrag: true,
    pinchToClose: true,
  });
  lightbox.init();
  lightbox.loadAndOpen(0);

  // Auto-clean: PhotoSwipe destroys itself on close, but the lightbox
  // wrapper keeps a reference. Drop ours when the user closes so
  // garbage collection can reclaim the bound listeners.
  lightbox.on("close", () => {
    setTimeout(() => lightbox.destroy(), 250);
  });
}
