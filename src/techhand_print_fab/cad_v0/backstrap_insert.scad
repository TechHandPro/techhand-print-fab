// ORIGINAL TechHand/PRINT trainer CAD v0 — training tool only
// Parametric backstrap insert S/M/L — original geometry, not OEM copy.
// Adds ~2 mm circumference-ish thickness steps via size=0/1/2.

/* ===== PARAMETERS ===== */
size = 1;                  // 0=S, 1=M, 2=L
base_thick = 3.0;          // core thickness (mm)
step_mm = 2.0;             // added thickness per size step (~circumference-ish)
insert_h = 95;             // height along grip (mm)
insert_w = 28;             // width matching grip back (mm)
tab_w = 10;                // retention tab width
tab_t = 2.2;               // retention tab thickness
tab_clear = 0.25;          // mate clearance
$fn = 48;

thick = base_thick + size * step_mm;

module backstrap() {
    // Curved outer face for palm comfort — original trainer contour
    hull() {
        translate([0, 0, 4])
            scale([1, thick/8, 1])
                cylinder(h=insert_h - 8, r=8);
        translate([-insert_w/2 + 2, 0, 6])
            cube([insert_w - 4, thick * 0.35, insert_h - 12]);
    }
    // Flat mating face against grip rear channel
    translate([-insert_w/2, -1.5, 0])
        cube([insert_w, 1.5 + 0.01, insert_h]);
    // Retention tabs (top/bottom) — dovetail-ish simple rectangular tabs
    for (z = [8, insert_h - 8 - 12]) {
        translate([-tab_w/2, -1.5 - tab_t, z])
            cube([tab_w, tab_t, 12]);
    }
    // Center locating rib
    translate([-3, -1.5 - 1.2, insert_h/2 - 20])
        cube([6, 1.2, 40]);
}

difference() {
    backstrap();
    // Lightening / flex slots (trainer aesthetic, not firearm)
    for (z = [20, 40, 60, 80]) {
        translate([-insert_w/2 + 4, thick * 0.15, z])
            cube([insert_w - 8, max(0.8, thick * 0.4), 3]);
    }
}

// Size label as raised emboss (optional visual)
translate([0, thick * 0.5, insert_h - 6])
    linear_extrude(0.6)
        text(size==0?"S":size==1?"M":"L", size=5, halign="center", valign="center");
