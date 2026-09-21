// ORIGINAL TechHand/PRINT trainer CAD v0 — training tool only
// Swappable compression spring seat / retainer cup.
// Starting springs: OD 8–10 mm, free length 12–20 mm, light dry-fire return rate.

/* ===== PARAMETERS ===== */
spring_od = 9.0;           // nominal spring OD (mm) — try 8, 9, 10
spring_id_clear = 0.3;     // diametral clearance over spring OD for pocket
seat_od = 14;              // outer diameter of seat cup
seat_h = 8;                // cup height
flange_od = 18;            // flange for retention in shell pocket
flange_h = 2.0;
guide_d = 4.5;             // optional center guide post OD (for spring ID ~5+)
guide_h = 6;
wall = 1.6;
$fn = 48;

pocket_id = spring_od + spring_id_clear;

difference() {
    union() {
        cylinder(h=flange_h, d=flange_od);
        translate([0, 0, flange_h])
            cylinder(h=seat_h, d=seat_od);
        // Center guide post (helps keep spring coaxial)
        translate([0, 0, flange_h])
            cylinder(h=guide_h, d=guide_d);
    }
    // Spring cavity annulus
    translate([0, 0, flange_h + 0.8])
        difference() {
            cylinder(h=seat_h, d=pocket_id);
            cylinder(h=seat_h + 0.1, d=guide_d + 1.2);
        }
}
