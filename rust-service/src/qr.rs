//! QR detection: decode a JPEG frame and return any QR payloads.

/// Decode QR codes from a single JPEG frame. Pure/CPU-bound; call via
/// `spawn_blocking`. Returns an empty vec on any decode error.
pub fn decode_qr(jpeg: &[u8]) -> Vec<String> {
    let dynamic = match image::load_from_memory_with_format(jpeg, image::ImageFormat::Jpeg) {
        Ok(img) => img,
        Err(_) => return Vec::new(),
    };
    let gray = dynamic.to_luma8();
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
