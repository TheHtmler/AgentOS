export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="skeleton-stack" aria-hidden>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="skeleton-line" />
      ))}
    </div>
  );
}
