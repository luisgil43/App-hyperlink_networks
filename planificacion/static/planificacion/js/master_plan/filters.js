document.addEventListener(
  "DOMContentLoaded",
  function () {

    const matrix =
      document.getElementById(
        "master-plan-matrix"
      );


    const panel =
      document.getElementById(
        "master-plan-excel-filter-panel"
      );


    const panelTitle =
      document.getElementById(
        "master-plan-excel-filter-title"
      );


    const searchInput =
      document.getElementById(
        "master-plan-excel-filter-search"
      );


    const optionsBox =
      document.getElementById(
        "master-plan-excel-filter-options"
      );


    const applyButton =
      document.getElementById(
        "master-plan-excel-filter-apply"
      );


    const clearButton =
      document.getElementById(
        "master-plan-excel-filter-clear"
      );


    const clearAllButton =
      document.getElementById(
        "master-plan-clear-filters"
      );


    const closeButton =
      document.getElementById(
        "master-plan-excel-filter-close"
      );


    if (
      !matrix
      ||
      !panel
      ||
      !optionsBox
    ) {
      return;
    }


    let currentKey =
      null;


    const activeFilters =
      {};


    /* =========================================================
       HELPERS
    ========================================================== */

    function getRows() {

      return Array.from(
        document.querySelectorAll(
          ".planning-activity-row-fixed"
        )
      );

    }


    function getTimelineRow(
      row
    ) {

      const rowIndex =
        row.dataset.rowIndex;


      return document.querySelector(
        ".planning-timeline-row[data-row-index='"
        +
        rowIndex
        +
        "']"
      );

    }


    function getRowValue(
      row,
      key
    ) {

      const attributeValue =
        row.getAttribute(
          "data-filter-"
          +
          key
        );


      if (
        attributeValue !== null
        &&
        attributeValue !== undefined
        &&
        String(
          attributeValue
        ).trim()
        !== ""
        &&
        String(
          attributeValue
        ).trim()
        !== "—"
      ) {

        return String(
          attributeValue
        ).trim();

      }


      const cell =
        row.querySelector(
          "[data-column-cell='"
          +
          key
          +
          "']"
        );


      if (
        cell
      ) {

        const content =
          cell.querySelector(
            "[data-column-content='"
            +
            key
            +
            "']"
          );


        const visibleValue =
          (
            content
              ?
              content.textContent
              :
              cell.textContent
          )
            .replace(
              /\s+/g,
              " "
            )
            .trim();


        if (
          visibleValue
        ) {

          return visibleValue;

        }

      }


      return "—";

    }


    function uniqueValuesForKey(
      key
    ) {

      const values =
        new Set();


      getRows().forEach(
        function (row) {

          values.add(
            getRowValue(
              row,
              key
            )
          );

        }
      );


      return Array.from(
        values
      ).sort(
        function (
          a,
          b
        ) {

          return a.localeCompare(
            b,
            undefined,
            {
              numeric: true,
              sensitivity: "base",
            }
          );

        }
      );

    }


    /* =========================================================
       ACTIVE COLUMN VISUAL STATE
    ========================================================== */

    function updateHeaderStates() {

      document.querySelectorAll(
        "[data-excel-column]"
      ).forEach(
        function (header) {

          const key =
            header.dataset.excelColumn;


          const filtered =
            (
              activeFilters[
                key
              ]
              &&
              activeFilters[
                key
              ].size
            );


          if (
            filtered
          ) {

            header.classList.add(
              "bg-blue-50"
            );


            const arrow =
              header.querySelector(
                ".master-plan-excel-arrow"
              );


            if (
              arrow
            ) {

              arrow.classList.remove(
                "text-gray-400"
              );


              arrow.classList.add(
                "text-blue-600"
              );

            }

          } else {

            header.classList.remove(
              "bg-blue-50"
            );


            const arrow =
              header.querySelector(
                ".master-plan-excel-arrow"
              );


            if (
              arrow
            ) {

              arrow.classList.remove(
                "text-blue-600"
              );


              arrow.classList.add(
                "text-gray-400"
              );

            }

          }

        }
      );


      if (
        clearAllButton
      ) {

        const hasFilters =
          Object.keys(
            activeFilters
          ).some(
            function (key) {

              return (
                activeFilters[
                  key
                ]
                &&
                activeFilters[
                  key
                ].size
              );

            }
          );


        if (
          hasFilters
        ) {

          clearAllButton.classList.remove(
            "text-gray-600"
          );


          clearAllButton.classList.add(
            "text-blue-700",
            "border-blue-300",
            "bg-blue-50"
          );

        } else {

          clearAllButton.classList.remove(
            "text-blue-700",
            "border-blue-300",
            "bg-blue-50"
          );


          clearAllButton.classList.add(
            "text-gray-600"
          );

        }

      }

    }


    /* =========================================================
       APPLY ALL EXCEL FILTERS
    ========================================================== */

    function applyFiltersToRows() {

      getRows().forEach(
        function (row) {

          let visible =
            true;


          Object.keys(
            activeFilters
          ).forEach(
            function (key) {

              const selected =
                activeFilters[
                  key
                ];


              if (
                !selected
                ||
                !selected.size
              ) {
                return;
              }


              const rowValue =
                getRowValue(
                  row,
                  key
                );


              if (
                !selected.has(
                  rowValue
                )
              ) {

                visible =
                  false;

              }

            }
          );


          /*
           * The existing Work filter also uses hidden.
           *
           * We intentionally only remove hidden when this row
           * matches the Excel filters. The current work-type
           * button will be reapplied immediately afterwards.
           */

          if (
            visible
          ) {

            row.classList.remove(
              "master-plan-excel-hidden"
            );

          } else {

            row.classList.add(
              "master-plan-excel-hidden"
            );

          }


          const timelineRow =
            getTimelineRow(
              row
            );


          const shouldHide =
            row.classList.contains(
              "master-plan-excel-hidden"
            );


          if (
            shouldHide
          ) {

            row.classList.add(
              "hidden"
            );


            if (
              timelineRow
            ) {

              timelineRow.classList.add(
                "hidden"
              );

            }

          } else {

            row.classList.remove(
              "hidden"
            );


            if (
              timelineRow
            ) {

              timelineRow.classList.remove(
                "hidden"
              );

            }

          }

        }
      );


      /*
       * Reapply current Work filter so both systems combine.
       */
      const activeWorkButton =
        Array.from(
          document.querySelectorAll(
            ".planning-work-type-btn"
          )
        ).find(
          function (button) {

            return button.classList.contains(
              "bg-gray-900"
            );

          }
        );


      if (
        activeWorkButton
      ) {

        const workType =
          activeWorkButton.dataset.workType;


        getRows().forEach(
          function (row) {

            if (
              row.classList.contains(
                "master-plan-excel-hidden"
              )
            ) {
              return;
            }


            const text =
              (
                row.dataset.activityText
                ||
                ""
              ).toLowerCase();


            let workVisible =
              true;


            if (
              workType
              ===
              "cabling"
            ) {

              workVisible =
                (
                  text.includes(
                    "cabl"
                  )
                  ||
                  text.includes(
                    "fiber placement"
                  )
                  ||
                  text.includes(
                    "placement"
                  )
                );

            } else if (
              workType
              ===
              "splicing"
            ) {

              workVisible =
                (
                  text.includes(
                    "splic"
                  )
                  ||
                  text.includes(
                    "test"
                  )
                );

            }


            const timelineRow =
              getTimelineRow(
                row
              );


            if (
              workVisible
            ) {

              row.classList.remove(
                "hidden"
              );


              if (
                timelineRow
              ) {

                timelineRow.classList.remove(
                  "hidden"
                );

              }

            } else {

              row.classList.add(
                "hidden"
              );


              if (
                timelineRow
              ) {

                timelineRow.classList.add(
                  "hidden"
                );

              }

            }

          }
        );

      }


      updateHeaderStates();


      window.dispatchEvent(
        new Event(
          "resize"
        )
      );

    }


    /* =========================================================
       OPTIONS
    ========================================================== */

    function renderOptions(
      key
    ) {

      optionsBox.innerHTML =
        "";


      const values =
        uniqueValuesForKey(
          key
        );


      const existing =
        activeFilters[
          key
        ];


      const allSelected =
        (
          !existing
          ||
          existing.size
          ===
          values.length
        );


      const selectAllLabel =
        document.createElement(
          "label"
        );


      selectAllLabel.className =
        "flex items-center gap-2 py-1.5 border-b border-gray-200 font-bold text-gray-700";


      selectAllLabel.innerHTML =
        "<input type='checkbox' id='master-plan-excel-select-all' "
        +
        (
          allSelected
            ?
            "checked"
            :
            ""
        )
        +
        ">"
        +
        "<span>(Select All)</span>";


      optionsBox.appendChild(
        selectAllLabel
      );


      values.forEach(
        function (value) {

          const label =
            document.createElement(
              "label"
            );


          label.className =
            "master-plan-excel-option flex items-center gap-2 py-1.5 text-gray-700";


          label.dataset.optionValue =
            value;


          const checked =
            (
              !existing
              ||
              existing.has(
                value
              )
            );


          const checkbox =
            document.createElement(
              "input"
            );


          checkbox.type =
            "checkbox";


          checkbox.className =
            "master-plan-excel-option-checkbox";


          checkbox.value =
            value;


          checkbox.checked =
            checked;


          const span =
            document.createElement(
              "span"
            );


          span.className =
            "truncate";


          span.textContent =
            value;


          label.appendChild(
            checkbox
          );


          label.appendChild(
            span
          );


          optionsBox.appendChild(
            label
          );

        }
      );


      const selectAll =
        document.getElementById(
          "master-plan-excel-select-all"
        );


      if (
        selectAll
      ) {

        selectAll.addEventListener(
          "change",
          function () {

            optionsBox.querySelectorAll(
              ".master-plan-excel-option"
            ).forEach(
              function (label) {

                if (
                  label.style.display
                  ===
                  "none"
                ) {
                  return;
                }


                const checkbox =
                  label.querySelector(
                    ".master-plan-excel-option-checkbox"
                  );


                if (
                  checkbox
                ) {

                  checkbox.checked =
                    selectAll.checked;

                }

              }
            );

          }
        );

      }

    }


    /* =========================================================
       SEARCH INSIDE FILTER PANEL
    ========================================================== */

    function filterOptions(
      query
    ) {

      const normalizedQuery =
        (
          query
          ||
          ""
        )
          .trim()
          .toLowerCase();


      optionsBox.querySelectorAll(
        ".master-plan-excel-option"
      ).forEach(
        function (label) {

          const value =
            (
              label.dataset.optionValue
              ||
              ""
            ).toLowerCase();


          const show =
            (
              !normalizedQuery
              ||
              value.includes(
                normalizedQuery
              )
            );


          label.style.display =
            show
              ?
              ""
              :
              "none";

        }
      );

    }


    /* =========================================================
       PANEL POSITION
    ========================================================== */

    function positionPanel(
      button
    ) {

      panel.classList.remove(
        "hidden"
      );


      const rect =
        button.getBoundingClientRect();


      const panelWidth =
        panel.offsetWidth
        ||
        270;


      const panelHeight =
        panel.offsetHeight
        ||
        300;


      let left =
        rect.left;


      let top =
        rect.bottom
        +
        5;


      const maximumLeft =
        window.innerWidth
        -
        panelWidth
        -
        8;


      if (
        left
        >
        maximumLeft
      ) {

        left =
          maximumLeft;

      }


      if (
        left
        <
        8
      ) {

        left =
          8;

      }


      const maximumTop =
        window.innerHeight
        -
        panelHeight
        -
        8;


      if (
        top
        >
        maximumTop
      ) {

        top =
          Math.max(
            8,
            rect.top
            -
            panelHeight
            -
            5
          );

      }


      panel.style.left =
        left
        +
        "px";


      panel.style.top =
        top
        +
        "px";

    }


    function closePanel() {

      panel.classList.add(
        "hidden"
      );


      currentKey =
        null;

    }


    /* =========================================================
       OPEN FILTER
    ========================================================== */

    document.querySelectorAll(
      ".master-plan-excel-header-btn"
    ).forEach(
      function (button) {

        button.addEventListener(
          "click",
          function (
            event
          ) {

            event.stopPropagation();


            currentKey =
              button.dataset.excelKey;


            if (
              !currentKey
            ) {
              return;
            }


            panelTitle.textContent =
              button.dataset.excelLabel
              ||
              currentKey;


            searchInput.value =
              "";


            renderOptions(
              currentKey
            );


            positionPanel(
              button
            );


            searchInput.focus();

          }
        );

      }
    );


    /* =========================================================
       PANEL SEARCH
    ========================================================== */

    if (
      searchInput
    ) {

      searchInput.addEventListener(
        "input",
        function () {

          filterOptions(
            searchInput.value
          );

        }
      );

    }


    /* =========================================================
       APPLY CURRENT COLUMN
    ========================================================== */

    if (
      applyButton
    ) {

      applyButton.addEventListener(
        "click",
        function () {

          if (
            !currentKey
          ) {
            return;
          }


          const values =
            uniqueValuesForKey(
              currentKey
            );


          const selected =
            new Set();


          optionsBox.querySelectorAll(
            ".master-plan-excel-option-checkbox"
          ).forEach(
            function (checkbox) {

              if (
                checkbox.checked
              ) {

                selected.add(
                  checkbox.value
                );

              }

            }
          );


          if (
            selected.size
            ===
            values.length
          ) {

            delete activeFilters[
              currentKey
            ];

          } else {

            activeFilters[
              currentKey
            ] =
              selected;

          }


          applyFiltersToRows();


          closePanel();

        }
      );

    }


    /* =========================================================
       CLEAR CURRENT COLUMN
    ========================================================== */

    if (
      clearButton
    ) {

      clearButton.addEventListener(
        "click",
        function () {

          if (
            currentKey
          ) {

            delete activeFilters[
              currentKey
            ];

          }


          applyFiltersToRows();


          closePanel();

        }
      );

    }


    /* =========================================================
       CLEAR ALL COLUMNS
    ========================================================== */

    if (
      clearAllButton
    ) {

      clearAllButton.addEventListener(
        "click",
        function () {

          Object.keys(
            activeFilters
          ).forEach(
            function (key) {

              delete activeFilters[
                key
              ];

            }
          );


          applyFiltersToRows();

        }
      );

    }


    /* =========================================================
       CLOSE
    ========================================================== */

    if (
      closeButton
    ) {

      closeButton.addEventListener(
        "click",
        function () {

          closePanel();

        }
      );

    }


    document.addEventListener(
      "click",
      function (
        event
      ) {

        if (
          panel.classList.contains(
            "hidden"
          )
        ) {
          return;
        }


        if (
          panel.contains(
            event.target
          )
        ) {
          return;
        }


        if (
          event.target.closest(
            ".master-plan-excel-header-btn"
          )
        ) {
          return;
        }


        closePanel();

      }
    );


    document.addEventListener(
      "keydown",
      function (
        event
      ) {

        if (
          event.key
          ===
          "Escape"
        ) {

          closePanel();

        }

      }
    );

  }
);