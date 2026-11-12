import type { Point } from "./api";

export function clampPoint([x, y]: Point, width: number, height: number): Point {
  return [Math.round(Math.min(Math.max(x, 0), width)), Math.round(Math.min(Math.max(y, 0), height))];
}

function orientation(a: Point, b: Point, c: Point): number {
  const value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1]);
  return value === 0 ? 0 : value > 0 ? 1 : 2;
}

function segmentsIntersect(p1: Point, p2: Point, p3: Point, p4: Point): boolean {
  const o1 = orientation(p1, p2, p3);
  const o2 = orientation(p1, p2, p4);
  const o3 = orientation(p3, p4, p1);
  const o4 = orientation(p3, p4, p2);
  return o1 !== o2 && o3 !== o4 && o1 !== 0 && o2 !== 0 && o3 !== 0 && o4 !== 0;
}

export function isSelfIntersecting(points: Point[]): boolean {
  const n = points.length;
  if (n < 4) return false;
  for (let i = 0; i < n; i++) {
    const a1 = points[i];
    const a2 = points[(i + 1) % n];
    for (let j = i + 1; j < n; j++) {
      if (Math.abs(i - j) <= 1 || (i === 0 && j === n - 1)) continue;
      if (segmentsIntersect(a1, a2, points[j], points[(j + 1) % n])) return true;
    }
  }
  return false;
}

export function pointInPolygon([x, y]: Point, polygon: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    const crosses = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi || 1e-9) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

/** Index of the edge nearest to p and the projected point, if within maxDistance. */
export function nearestEdge(points: Point[], p: Point, maxDistance: number): { index: number; point: Point } | null {
  let best: { index: number; point: Point; distance: number } | null = null;
  for (let i = 0; i < points.length; i++) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const length = dx * dx + dy * dy || 1;
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length));
    const projected: Point = [a[0] + t * dx, a[1] + t * dy];
    const distance = Math.hypot(projected[0] - p[0], projected[1] - p[1]);
    if (distance <= maxDistance && (!best || distance < best.distance)) {
      best = { index: i, point: projected, distance };
    }
  }
  return best ? { index: best.index, point: best.point } : null;
}

export function polygonWarnings(capture: Point[], inference: Point[]): string[] {
  const warnings: string[] = [];
  if (capture.length >= 3 && isSelfIntersecting(capture)) warnings.push("Vùng capture tự cắt nhau");
  if (inference.length >= 3 && isSelfIntersecting(inference)) warnings.push("Vùng inference tự cắt nhau");
  if (capture.length >= 3 && inference.length >= 3) {
    // Tolerate vertices shared with the inference polygon's border.
    const outside = capture.filter(
      (point) => !pointInPolygon(point, inference) && !inference.some((q) => q[0] === point[0] && q[1] === point[1]),
    );
    if (outside.length) warnings.push(`Có ${outside.length} điểm của vùng capture nằm ngoài vùng inference`);
  }
  return warnings;
}
