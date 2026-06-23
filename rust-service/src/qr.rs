//! QR detection: decode a JPEG frame and return any QR payloads.
//!
//! Difficult captures (screen banding, glare, uneven lighting) are handled with
//! a multi-pass approach: try the frame as-is first (cheap, usually enough), and
//! only if that fails apply progressively stronger contrast normalization.

use image::GrayImage;

/// Decode QR codes from a single JPEG frame. Pure/CPU-bound; call via
/// `spawn_blocking`. Returns an empty vec on any decode error.
pub fn decode_qr(jpeg: &[u8]) -> Vec<String> {
    let dynamic = match image::load_from_memory_with_format(jpeg, image::ImageFormat::Jpeg) {
        Ok(img) => img,
        Err(_) => return Vec::new(),
    };
    let gray = dynamic.to_luma8();

    // Pass 1: as captured (rqrr applies its own binarization). Fast path.
    let found = detect(&gray);
    if !found.is_empty() {
        return found;
    }

    // Pass 2: global histogram equalization — rescues dim / low-contrast frames.
    let found = detect(&imageproc::contrast::equalize_histogram(&gray));
    if !found.is_empty() {
        return found;
    }

    // Pass 3: local adaptive threshold — best for banding / glare / shadows,
    // where one region is much brighter than another.
    let radius = (gray.width().min(gray.height()) / 16).clamp(8, 40);
    detect(&imageproc::contrast::adaptive_threshold(&gray, radius))
}

/// Run rqrr on a grayscale image and collect decoded payloads.
fn detect(gray: &GrayImage) -> Vec<String> {
    let (width, height) = gray.dimensions();
    // Use prepare_from_greyscale to avoid coupling to rqrr's `image` version.
    let mut prepared = rqrr::PreparedImage::prepare_from_greyscale(
        width as usize,
        height as usize,
        |x, y| gray.get_pixel(x as u32, y as u32)[0],
    );

    let mut payloads = Vec::new();
    for grid in prepared.detect_grids() {
        if let Ok((_meta, content)) = grid.decode() {
            payloads.push(content);
        }
    }
    payloads
}
