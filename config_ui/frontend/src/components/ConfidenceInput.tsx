import { Col, InputNumber, Row, Slider } from "antd";

interface Props {
  value?: number;
  onChange?: (value: number) => void;
  disabled?: boolean;
}

/** 0..1 slider with a numeric box; usable as a controlled antd Form.Item child. */
export default function ConfidenceInput({ value, onChange, disabled }: Props) {
  return (
    <Row gutter={8} align="middle">
      <Col flex="auto">
        <Slider min={0} max={1} step={0.01} value={value} onChange={(v) => onChange?.(v)} disabled={disabled} />
      </Col>
      <Col flex="88px">
        <InputNumber
          min={0}
          max={1}
          step={0.01}
          precision={2}
          value={value}
          onChange={(v) => v !== null && onChange?.(v)}
          disabled={disabled}
          style={{ width: "100%" }}
        />
      </Col>
    </Row>
  );
}
