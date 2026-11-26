/** Minimal class-name joiner (falsy values are dropped). */
export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}
