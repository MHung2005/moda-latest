export default function DiffView({ diff }: { diff: string }) {
  if (!diff) return <em>Không có khác biệt</em>;
  return (
    <pre className="diff-view">
      {diff.split("\n").map((line, index) => {
        let className = "";
        if (line.startsWith("+") && !line.startsWith("+++")) className = "diff-add";
        else if (line.startsWith("-") && !line.startsWith("---")) className = "diff-del";
        else if (line.startsWith("@@")) className = "diff-hunk";
        return (
          <div key={index} className={className}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}
