import { notFound } from "next/navigation";
import { StockDetail } from "../../../components/StockDetail";

export default async function StockPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  if (!/^[03468][0-9]{5}$/.test(code)) notFound();
  return <StockDetail key={code} code={code} />;
}
