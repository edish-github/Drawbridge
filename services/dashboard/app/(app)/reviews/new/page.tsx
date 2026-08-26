/**
 * Open a review.
 *
 * The form asks for the two things a tier decision is computed from — declared data categories
 * and declared system access — as checkboxes and a select, never as prose. The description box is
 * there and is deliberately not consulted by the tiering rules: the person filling this in wants
 * the contract signed, so what they wrote as a sentence must not be able to lower the scrutiny
 * applied to the vendor it describes.
 *
 * The page says so, because a form that quietly ignores a field it asked for is worse than one
 * that explains why it wants it anyway.
 */

import { requireCapability, CAN_ACT } from "../../../../lib/guard";
import { PageHead } from "../../../components";
import IntakeForm from "./form";

export const dynamic = "force-dynamic";

export default async function NewReview() {
  await requireCapability(CAN_ACT);

  return (
    <>
      <PageHead
        crumb="Reviews / New"
        title="Open a review"
        subtitle="Tell us what the vendor will handle. The tier, the questions and the evidence requested all follow from it."
      />
      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <IntakeForm />
      </div>
    </>
  );
}
