use crate::{
    geometry::{Point, Ring},
    parser::Result,
};

// Code 39 characters in Modulo 43 order. Each set bit marks a wide element.
const ALPHABET: &[u8] = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%";
const PATTERNS: [u16; 43] = [
    0x034, 0x121, 0x061, 0x160, 0x031, 0x130, 0x070, 0x025, 0x124, 0x064, 0x109, 0x049, 0x148,
    0x019, 0x118, 0x058, 0x00D, 0x10C, 0x04C, 0x01C, 0x103, 0x043, 0x142, 0x013, 0x112, 0x052,
    0x007, 0x106, 0x046, 0x016, 0x181, 0x0C1, 0x1C0, 0x091, 0x190, 0x0D0, 0x085, 0x184, 0x0C4,
    0x0A8, 0x0A2, 0x08A, 0x02A,
];
const GUARD: u16 = 0x094;

fn extend_ascii(value: u8, encoded: &mut Vec<u8>) -> Result<()> {
    let (shift, letter) = match value {
        b' ' | b'-' | b'.' | b'0'..=b'9' | b'A'..=b'Z' => {
            encoded.push(value);
            return Ok(());
        }
        0 => (b'%', b'U'),
        1..=26 => (b'$', b'A' + value - 1),
        27..=31 => (b'%', b'A' + value - 27),
        b'!'..=b',' | b'/' | b':' => (b'/', b'A' + value - b'!'),
        b';'..=b'?' => (b'%', b'F' + value - b';'),
        b'@' => (b'%', b'V'),
        b'['..=b'_' => (b'%', b'K' + value - b'['),
        b'`' => (b'%', b'W'),
        b'a'..=b'z' => (b'+', b'A' + value - b'a'),
        b'{'..=127 => (b'%', b'P' + value - b'{'),
        _ => return Err("UPC39 Full ASCII payload contains a non-ASCII character".into()),
    };
    encoded.extend([shift, letter]);
    Ok(())
}

pub(super) fn region(
    value: &str,
    (element, height): Point,
    full_ascii: bool,
    checksum: bool,
    background: bool,
) -> Result<(Vec<Ring>, f64)> {
    let mut data = Vec::new();
    if full_ascii {
        for byte in value.bytes() {
            extend_ascii(byte, &mut data)?;
        }
    } else {
        data.extend_from_slice(value.as_bytes());
    }
    let mut characters = Vec::with_capacity(data.len() + 3);
    characters.push(GUARD);
    let mut check = 0;
    for byte in data {
        let index = ALPHABET.iter().position(|&c| c == byte).ok_or_else(|| {
            format!(
                "character {:?} is outside the UPC39 alphabet",
                char::from(byte)
            )
        })?;
        characters.push(PATTERNS[index]);
        check = (check + index) % ALPHABET.len();
    }
    if checksum {
        characters.push(PATTERNS[check]);
    }
    characters.push(GUARD);

    let mut rings = Vec::new();
    let mut module = 0;
    let mut paint = |width: usize, bar: bool| {
        let left = module as f64 * element;
        module += width;
        if bar != background {
            let right = module as f64 * element;
            rings.push((
                vec![(left, 0.), (left, height), (right, height), (right, 0.)],
                false,
            ));
        }
    };
    for (i, pattern) in characters.into_iter().enumerate() {
        if i > 0 {
            paint(1, false);
        }
        for bit in 0..9 {
            paint(
                if pattern & (1 << (8 - bit)) == 0 {
                    1
                } else {
                    2
                },
                bit % 2 == 0,
            );
        }
    }
    Ok((rings, module as f64 * element))
}
