# Geometry
For width W in points and target density P pixels/inch, desired pixels = ceil(W / 72 * P). Use the maximum W across all placements, cap at native image width, then scale height by the same ratio. Cropping can show only a narrow window while the underlying full picture is much wider; using the window width would over-downsample.

Some renderers rescale oversized original images on insertion. Measure the geometry in the actual renderer or reconstruct the renderer's sizing rules before planning tiles. Preserve positions, crop rectangles, z-order and slide sizes; compare with a documented tolerance suitable for the renderer.

The helper writes PNG using LANCZOS; rejects nonpositive/nonfinite geometry; applies EXIF orientation to the derivative; preserves alpha for transparent inputs; rejects multiple frames; records raw source dimensions and oriented dimensions. Exact source bytes are hashed and never changed. A larger output is still possible for originally compressed JPEGs.

Regression scenarios: native image smaller than target; repeated placements; portrait long screenshot; transparent source; EXIF rotation; invalid PPI; existing output; input/output collision. Inspect small screenshot text after actual PPT export, not just the source PNG.

Origin: engine/v9/ppt_images.py. Image transformations for this deterministic report optimization are code operations, not generated illustrations.
