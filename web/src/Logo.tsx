/** The CohortLens mark: a lens whose bars step down, like an attrition waterfall. Decorative; the name is beside it. */
export function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" fill="#4f46e5" />
      <circle cx="14" cy="14" r="8" fill="none" stroke="#fff" strokeWidth="2.5" />
      <path d="M11 10h6M11 14h4M11 18h2" stroke="#c7d2fe" strokeWidth="2" strokeLinecap="round" />
      <path d="M20 20l6 6" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
