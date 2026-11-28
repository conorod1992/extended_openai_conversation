// HTML replaces NULs and normalizes carriage returns in attribute values.
const encoded = {"%":"%25", "\0":"%00", "\r":"%0D"};
const decoded = {"%25":"%", "%00":"\0", "%0D":"\r"};

export const encodeCollectionId = value => String(value).replace(/[%\0\r]/g, char => encoded[char]);
export const decodeCollectionId = value => value?.replace(/%25|%00|%0D/g, code => decoded[code]);
