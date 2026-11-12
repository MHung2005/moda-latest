import { useCallback, useEffect, useRef, useState } from "react";
import { Circle, Image as KonvaImage, Layer, Line, Rect, Stage, Text } from "react-konva";
import type Konva from "konva";
import type { Point } from "../api";
import { clampPoint, nearestEdge } from "../geometry";

/** Load an image URL; returns the element (or null) and an error flag. */
export function useImage(url: string | null): { image: HTMLImageElement | null; failed: boolean; loading: boolean } {
  const [state, setState] = useState<{ image: HTMLImageElement | null; failed: boolean; loading: boolean }>({
    image: null,
    failed: false,
    loading: false,
  });
  useEffect(() => {
    if (!url) {
      setState({ image: null, failed: false, loading: false });
      return;
    }
    let cancelled = false;
    setState((previous) => ({ ...previous, loading: true, failed: false }));
    const element = new window.Image();
    element.onload = () => !cancelled && setState({ image: element, failed: false, loading: false });
    element.onerror = () => !cancelled && setState({ image: null, failed: true, loading: false });
    element.src = url;
    return () => {
      cancelled = true;
    };
  }, [url]);
  return state;
}

function useContainerWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

export interface Overlay {
  points: Point[];
  color: string;
  dashed?: boolean;
  label?: string;
}

interface PolygonProps {
  image: HTMLImageElement | null;
  /** Coordinate space of the points, e.g. 2688x1520 (DeepStream streammux). */
  width: number;
  height: number;
  points: Point[];
  color: string;
  dashed?: boolean;
  overlays?: Overlay[];
  readOnly?: boolean;
  /** When true, clicks add points instead of editing. */
  drawing: boolean;
  onDrawingDone: () => void;
  onChange: (points: Point[]) => void;
}

export function PolygonEditor(props: PolygonProps) {
  const { image, width, height, points, color, dashed, overlays = [], readOnly, drawing, onDrawingDone, onChange } = props;
  const [containerRef, displayWidth] = useContainerWidth();
  const [draft, setDraft] = useState<Point[]>([]);
  const [hover, setHover] = useState<Point | null>(null);
  const scale = displayWidth / width;
  const handle = 6; // on-screen radius in px

  useEffect(() => {
    if (!drawing) {
      setDraft([]);
      setHover(null);
    }
  }, [drawing]);

  const toImage = (stage: Konva.Stage): Point | null => {
    const position = stage.getPointerPosition();
    if (!position) return null;
    return clampPoint([position.x / scale, position.y / scale], width, height);
  };

  const finishDraft = useCallback(() => {
    if (draft.length >= 3) onChange(draft);
    setDraft([]);
    onDrawingDone();
  }, [draft, onChange, onDrawingDone]);

  useEffect(() => {
    if (!drawing) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Enter") finishDraft();
      if (event.key === "Escape") {
        setDraft([]);
        onDrawingDone();
      }
      if (event.key === "Backspace") setDraft((current) => current.slice(0, -1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawing, finishDraft, onDrawingDone]);

  const onStageClick = (event: Konva.KonvaEventObject<MouseEvent>) => {
    if (!drawing || readOnly || event.evt.button !== 0) return;
    const point = toImage(event.target.getStage()!);
    if (!point) return;
    if (draft.length >= 3) {
      const [fx, fy] = draft[0];
      if (Math.hypot(fx - point[0], fy - point[1]) * scale < 12) {
        finishDraft();
        return;
      }
    }
    setDraft((current) => [...current, point]);
  };

  const onStageDoubleClick = (event: Konva.KonvaEventObject<MouseEvent>) => {
    if (drawing || readOnly) return;
    const point = toImage(event.target.getStage()!);
    if (!point) return;
    const edge = nearestEdge(points, point, 15 / scale);
    if (!edge) return;
    const next = [...points];
    next.splice(edge.index + 1, 0, clampPoint(edge.point, width, height));
    onChange(next);
  };

  const flat = (list: Point[]) => list.flatMap(([x, y]) => [x * scale, y * scale]);
  const shown = drawing ? draft : points;

  return (
    <div ref={containerRef} className="roi-stage">
      <Stage
        width={displayWidth}
        height={height * scale}
        onClick={onStageClick}
        onDblClick={onStageDoubleClick}
        onMouseMove={(event) => drawing && setHover(toImage(event.target.getStage()!))}
        onContextMenu={(event) => event.evt.preventDefault()}
        style={{ cursor: drawing ? "crosshair" : "default" }}
      >
        <Layer listening={false}>
          {image && <KonvaImage image={image} width={displayWidth} height={height * scale} />}
          {!image && (
            <Text
              text="Chưa có ảnh camera. Bấm “Chụp ảnh” để lấy snapshot."
              x={16}
              y={16}
              fill="#bbb"
              fontSize={14}
            />
          )}
          {overlays.map((overlay, index) =>
            overlay.points.length >= 2 ? (
              <Line
                key={index}
                points={flat(overlay.points)}
                closed
                stroke={overlay.color}
                strokeWidth={2}
                dash={overlay.dashed ? [8, 6] : undefined}
                fill={overlay.color + "22"}
                opacity={0.7}
              />
            ) : null,
          )}
        </Layer>
        <Layer>
          {shown.length >= 2 && (
            <Line
              points={flat(drawing && hover ? [...draft, hover] : shown)}
              closed={!drawing}
              stroke={color}
              strokeWidth={2.5}
              dash={dashed ? [10, 6] : undefined}
              fill={drawing ? undefined : color + "40"}
              listening={false}
            />
          )}
          {shown.map(([x, y], index) => (
            <Circle
              key={`${index}-${x}-${y}`}
              x={x * scale}
              y={y * scale}
              radius={drawing && index === 0 && draft.length >= 3 ? handle * 1.6 : handle}
              fill={index === 0 ? "#fff" : color}
              stroke={color}
              strokeWidth={2}
              draggable={!readOnly && !drawing}
              onDragMove={(event) => {
                const next = [...points];
                next[index] = clampPoint([event.target.x() / scale, event.target.y() / scale], width, height);
                event.target.position({ x: next[index][0] * scale, y: next[index][1] * scale });
                onChange(next);
              }}
              onContextMenu={(event) => {
                event.evt.preventDefault();
                if (readOnly || drawing || points.length <= 3) return;
                onChange(points.filter((_, i) => i !== index));
              }}
              onMouseEnter={(event) => {
                const stage = event.target.getStage();
                if (stage && !readOnly && !drawing) stage.container().style.cursor = "move";
              }}
              onMouseLeave={(event) => {
                const stage = event.target.getStage();
                if (stage) stage.container().style.cursor = drawing ? "crosshair" : "default";
              }}
            />
          ))}
        </Layer>
      </Stage>
    </div>
  );
}

export interface Box {
  x_min: number;
  x_max: number;
  y_min: number;
  y_max: number;
}

interface RectProps {
  image: HTMLImageElement | null;
  width: number;
  height: number;
  box: Box | null;
  color: string;
  readOnly?: boolean;
  onChange: (box: Box) => void;
}

/** Axis-aligned crop editor: drag on empty area to draw, drag corners to resize. */
export function RectEditor({ image, width, height, box, color, readOnly, onChange }: RectProps) {
  const [containerRef, displayWidth] = useContainerWidth();
  const [start, setStart] = useState<Point | null>(null);
  const scale = displayWidth / width;

  const pointer = (stage: Konva.Stage): Point | null => {
    const position = stage.getPointerPosition();
    return position ? clampPoint([position.x / scale, position.y / scale], width, height) : null;
  };

  const normalise = (a: Point, b: Point): Box => ({
    x_min: Math.min(a[0], b[0]),
    x_max: Math.max(a[0], b[0]),
    y_min: Math.min(a[1], b[1]),
    y_max: Math.max(a[1], b[1]),
  });

  const corners: [keyof Box, keyof Box][] = [
    ["x_min", "y_min"],
    ["x_max", "y_min"],
    ["x_max", "y_max"],
    ["x_min", "y_max"],
  ];

  return (
    <div ref={containerRef} className="roi-stage">
      <Stage
        width={displayWidth}
        height={height * scale}
        style={{ cursor: readOnly ? "default" : "crosshair" }}
        onMouseDown={(event) => {
          if (readOnly || event.target.getClassName() === "Circle") return;
          setStart(pointer(event.target.getStage()!));
        }}
        onMouseMove={(event) => {
          if (!start) return;
          const current = pointer(event.target.getStage()!);
          if (current && Math.abs(current[0] - start[0]) > 2 && Math.abs(current[1] - start[1]) > 2) {
            onChange(normalise(start, current));
          }
        }}
        onMouseUp={() => setStart(null)}
        onMouseLeave={() => setStart(null)}
      >
        <Layer listening={false}>
          {image && <KonvaImage image={image} width={displayWidth} height={height * scale} />}
          {!image && <Text text="Chưa có ảnh camera cân." x={16} y={16} fill="#bbb" fontSize={14} />}
          {box && (
            <Rect
              x={box.x_min * scale}
              y={box.y_min * scale}
              width={(box.x_max - box.x_min) * scale}
              height={(box.y_max - box.y_min) * scale}
              stroke={color}
              strokeWidth={2.5}
              fill={color + "33"}
            />
          )}
        </Layer>
        <Layer>
          {box &&
            !readOnly &&
            corners.map(([kx, ky]) => (
              <Circle
                key={`${kx}-${ky}`}
                x={box[kx] * scale}
                y={box[ky] * scale}
                radius={7}
                fill="#fff"
                stroke={color}
                strokeWidth={2}
                draggable
                onDragMove={(event) => {
                  const [x, y] = clampPoint([event.target.x() / scale, event.target.y() / scale], width, height);
                  const next = { ...box, [kx]: x, [ky]: y };
                  const fixed = normalise([next.x_min, next.y_min], [next.x_max, next.y_max]);
                  event.target.position({ x: next[kx] * scale, y: next[ky] * scale });
                  onChange(fixed);
                }}
              />
            ))}
        </Layer>
      </Stage>
    </div>
  );
}
